"""
Localización exacta de citas dentro del PDF original (revisión humana).

Para cada cita del documento (leída de Neo4j) busca su texto en el PDF con
PyMuPDF `page.search_for` y devuelve la página real (1-based) más los
rectángulos donde aparece, con las dimensiones de página para que el frontend
escale el overlay de resaltado.

La misma cita "(Autor, año)" puede aparecer varias veces en el documento, así
que primero se busca por CONTEXTO: la cola del fragmento_oracion que termina
en la cita, que es (casi) única. Sin contexto utilizable, las ocurrencias del
texto se reparten en orden entre las citas que comparten el mismo texto, para
que dos cartillas iguales no apunten a la misma página.

Si el texto exacto de la cita no aparece (p. ej. el LLM lo normalizó), se cae
a variantes construidas a partir del apellido y el año: "Apellido (año)",
"Apellido, año" y "(Apellido, año)". Sin match → pagina_real=None, rects=[].

El resultado se cachea en Storage (ubicaciones/{documento_id}.json) y se
invalida cuando se edita/crea/elimina una cita.
"""
import json
import re

import structlog

from app.models.grafo import UbicacionCita, RectResaltado
from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service
from app.services.storage.supabase_storage_service import storage_service

logger = structlog.get_logger(__name__)
settings = get_settings()


# Versión del formato/algoritmo del cache de ubicaciones. Se incrementa al
# cambiar la localización para recalcular caches viejos (p. ej. los que
# asignaban todas las citas duplicadas a la primera ocurrencia).
_VERSION_CACHE = 2


def _objeto_cache(documento_id: str) -> str:
    return f"ubicaciones/{documento_id}.json"


def _objeto_pdf(documento_id: str) -> str:
    return f"uploads/{documento_id}.pdf"


# Términos del encabezado de la sección de referencias, por especificidad.
# search_for es case-insensitive; se toma la ÚLTIMA página con match porque
# el término también aparece antes en el índice y en menciones del cuerpo.
_TERMINOS_REFERENCIAS = [
    "referencias bibliográficas",
    "lista de referencias",
    "referencias",
    "bibliografía",
    "references",
    "bibliography",
]


def _variantes_busqueda(texto_cita: str) -> list[str]:
    """
    Variantes del texto de la cita para buscar en el PDF, en orden de
    preferencia. Nunca altera el texto original: solo genera alternativas.
    """
    variantes = [texto_cita.strip()]

    # Sin paréntesis envolventes: "(Pérez, 2020)" → "Pérez, 2020"
    sin_parentesis = texto_cita.strip().strip("()").strip()
    if sin_parentesis and sin_parentesis not in variantes:
        variantes.append(sin_parentesis)

    anio_m = re.search(r"\b(19|20)\d{2}[a-z]?\b", texto_cita)
    apellido_m = re.search(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ\-]+", texto_cita)
    if anio_m and apellido_m:
        apellido, anio = apellido_m.group(), anio_m.group()
        for candidata in (
            f"{apellido} ({anio})",
            f"{apellido}, {anio}",
            f"({apellido}, {anio})",
        ):
            if candidata not in variantes:
                variantes.append(candidata)

    return variantes


class LocalizacionService:

    def obtener_ubicaciones(self, documento_id: str) -> dict | None:
        """
        Ubicaciones de todas las citas + página de la sección de referencias.
        Retorna {"pagina_referencias": int|None, "ubicaciones": [UbicacionCita]}
        usando el cache de Storage si existe. None si el PDF no existe.
        """
        crudo = storage_service.leer_texto(_objeto_cache(documento_id))
        if crudo is not None:
            try:
                datos = json.loads(crudo)
                # Los caches de formatos/algoritmos viejos se recalculan.
                if isinstance(datos, dict) and datos.get("version") == _VERSION_CACHE:
                    return {
                        "pagina_referencias": datos.get("pagina_referencias"),
                        "ubicaciones": [UbicacionCita(**u) for u in datos["ubicaciones"]],
                    }
            except Exception as e:
                logger.warning("cache_ubicaciones_corrupto", doc_id=documento_id, error=str(e))

        ruta_pdf = storage_service.obtener_local(_objeto_pdf(documento_id))
        if ruta_pdf is None:
            return None

        citas = self._leer_citas(documento_id)
        ubicaciones, pagina_referencias = self._localizar(str(ruta_pdf), citas)

        storage_service.subir_texto(
            _objeto_cache(documento_id),
            json.dumps(
                {
                    "version": _VERSION_CACHE,
                    "pagina_referencias": pagina_referencias,
                    "ubicaciones": [u.model_dump() for u in ubicaciones],
                },
                ensure_ascii=False,
            ),
        )
        return {"pagina_referencias": pagina_referencias, "ubicaciones": ubicaciones}

    def invalidar_cache(self, documento_id: str) -> None:
        """Se llama al editar/crear/eliminar citas para recalcular en la próxima lectura."""
        storage_service.eliminar(_objeto_cache(documento_id))

    def sincronizar_paginas(self, documento_id: str) -> int:
        """
        Corrige la página de cada Cita en Neo4j con la página REAL del PDF
        (la extracción solo estima por posición del bloque). Las citas no
        localizadas conservan su estimación. Además deja el cache de
        ubicaciones listo para la fase de revisión. Retorna cuántas actualizó.
        """
        datos = self.obtener_ubicaciones(documento_id)
        ubicaciones = datos["ubicaciones"] if datos else []
        if not ubicaciones:
            return 0

        actualizadas = 0
        query = "MATCH (c:Cita {id: $cita_id}) SET c.pagina = $pagina"
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            for u in ubicaciones:
                if u.pagina_real is not None:
                    session.run(query, cita_id=u.cita_id, pagina=u.pagina_real)
                    actualizadas += 1

        logger.info(
            "paginas_sincronizadas",
            doc_id=documento_id,
            actualizadas=actualizadas,
            sin_localizar=len(ubicaciones) - actualizadas,
        )
        return actualizadas

    # ── Internos ──────────────────────────────────────────────────────────────

    def _leer_citas(self, documento_id: str) -> list[dict]:
        query = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
        RETURN c.id AS cita_id, c.texto AS texto_cita,
               c.fragmento AS fragmento, c.pagina AS pagina
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            return [
                {
                    "cita_id": rec["cita_id"],
                    "texto_cita": rec["texto_cita"] or "",
                    "fragmento": rec["fragmento"] or "",
                    "pagina": rec["pagina"] or 0,
                }
                for rec in session.run(query, doc_id=documento_id)
            ]

    def _localizar(
        self, ruta_pdf: str, citas: list[dict]
    ) -> tuple[list[UbicacionCita], int | None]:
        import fitz

        with fitz.open(ruta_pdf) as doc:
            # Las citas se procesan en orden de página estimada para que las
            # ocurrencias de un mismo texto se repartan en orden de aparición.
            orden = sorted(range(len(citas)), key=lambda i: citas[i]["pagina"])
            resultados: dict[int, UbicacionCita] = {}
            ocurrencias_por_texto: dict[str, list] = {}
            siguiente_ocurrencia: dict[str, int] = {}
            for i in orden:
                resultados[i] = self._localizar_cita(
                    doc, citas[i], ocurrencias_por_texto, siguiente_ocurrencia
                )
            ubicaciones = [resultados[i] for i in range(len(citas))]
            pagina_referencias = self._pagina_referencias(doc)

        localizadas = sum(1 for u in ubicaciones if u.pagina_real is not None)
        logger.info(
            "citas_localizadas",
            total=len(ubicaciones),
            localizadas=localizadas,
            no_localizadas=len(ubicaciones) - localizadas,
            pagina_referencias=pagina_referencias,
        )
        return ubicaciones, pagina_referencias

    @staticmethod
    def _pagina_referencias(doc) -> int | None:
        """Página (1-based) donde empieza la sección de referencias del PDF."""
        for termino in _TERMINOS_REFERENCIAS:
            paginas = [
                numero
                for numero, page in enumerate(doc, start=1)
                if page.search_for(termino)
            ]
            if paginas:
                return paginas[-1]
        return None

    def _localizar_cita(
        self,
        doc,
        cita: dict,
        ocurrencias_por_texto: dict[str, list],
        siguiente_ocurrencia: dict[str, int],
    ) -> UbicacionCita:
        texto_cita = cita["texto_cita"].strip()
        no_localizada = UbicacionCita(
            cita_id=cita["cita_id"], texto_cita=cita["texto_cita"],
            pagina_real=None, rects=[],
        )
        if not texto_cita:
            return no_localizada

        # 1) El fragmento (aseveración) da contexto único: distingue entre
        #    varias ocurrencias del mismo "(Autor, año)" en el documento.
        ubicacion = self._localizar_por_contexto(doc, cita)
        if ubicacion is not None:
            return ubicacion

        # 2) Sin contexto utilizable: las ocurrencias del texto se reparten
        #    en orden entre las citas que comparten el mismo texto.
        if texto_cita not in ocurrencias_por_texto:
            ocurrencias_por_texto[texto_cita] = self._ocurrencias(doc, texto_cita)
        ocurrencias = ocurrencias_por_texto[texto_cita]
        if not ocurrencias:
            return no_localizada

        idx = min(siguiente_ocurrencia.get(texto_cita, 0), len(ocurrencias) - 1)
        siguiente_ocurrencia[texto_cita] = idx + 1
        numero, rect, ancho, alto = ocurrencias[idx]
        return UbicacionCita(
            cita_id=cita["cita_id"],
            texto_cita=cita["texto_cita"],
            pagina_real=numero,
            rects=[RectResaltado(
                pagina=numero,
                x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1,
                ancho_pagina=ancho, alto_pagina=alto,
            )],
        )

    # El contexto debe aportar caracteres propios además de la cita para
    # considerarse distintivo.
    _MIN_CONTEXTO_EXTRA = 12

    def _localizar_por_contexto(self, doc, cita: dict) -> UbicacionCita | None:
        """
        Busca la cola del fragmento_oracion que termina en la cita (~60 chars
        de contexto + la cita). Ese texto es (casi) único en el documento, así
        que resuelve la página correcta cuando el mismo "(Autor, año)" aparece
        varias veces. None si el fragmento no sirve o no hay match.
        """
        texto_cita = cita["texto_cita"].strip()
        fragmento = re.sub(r"\s+", " ", cita.get("fragmento") or "").strip()
        pos = fragmento.rfind(texto_cita)
        if pos == -1:
            return None
        fin = pos + len(texto_cita)
        contexto = fragmento[max(0, pos - 60):fin].strip()
        if len(contexto) < len(texto_cita) + self._MIN_CONTEXTO_EXTRA:
            return None

        for numero, page in enumerate(doc, start=1):
            hits_contexto = page.search_for(contexto)
            if not hits_contexto:
                continue
            # Rects de la cita dentro del contexto encontrado; si la búsqueda
            # fina falla, se resalta el último renglón del contexto (donde
            # termina la cita).
            hits_cita = [
                h for h in page.search_for(texto_cita)
                if any(h.intersects(hc) for hc in hits_contexto)
            ] or [hits_contexto[-1]]
            return UbicacionCita(
                cita_id=cita["cita_id"],
                texto_cita=cita["texto_cita"],
                pagina_real=numero,
                rects=[RectResaltado(
                    pagina=numero,
                    x0=h.x0, y0=h.y0, x1=h.x1, y1=h.y1,
                    ancho_pagina=page.rect.width,
                    alto_pagina=page.rect.height,
                ) for h in hits_cita],
            )
        return None

    @staticmethod
    def _ocurrencias(doc, texto_cita: str) -> list[tuple[int, object, float, float]]:
        """
        Todas las ocurrencias (página, rect, ancho, alto) de la primera
        variante del texto con matches, en orden de aparición en el documento.
        """
        for variante in _variantes_busqueda(texto_cita):
            ocurrencias = []
            for numero, page in enumerate(doc, start=1):
                for h in page.search_for(variante):
                    ocurrencias.append((numero, h, page.rect.width, page.rect.height))
            if ocurrencias:
                return ocurrencias
        return []


# Singleton
localizacion_service = LocalizacionService()

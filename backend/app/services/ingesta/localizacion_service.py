"""
Localización exacta de citas dentro del PDF original (revisión humana).

Para cada cita del documento (leída de Neo4j) busca su texto en el PDF con
PyMuPDF `page.search_for` y devuelve la página real (1-based) más los
rectángulos donde aparece, con las dimensiones de página para que el frontend
escale el overlay de resaltado.

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


def _objeto_cache(documento_id: str) -> str:
    return f"ubicaciones/{documento_id}.json"


def _objeto_pdf(documento_id: str) -> str:
    return f"uploads/{documento_id}.pdf"


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

    def obtener_ubicaciones(self, documento_id: str) -> list[UbicacionCita] | None:
        """
        Ubicaciones de todas las citas del documento. Usa el cache de Storage
        si existe; si no, localiza y persiste. None si el PDF no existe.
        """
        crudo = storage_service.leer_texto(_objeto_cache(documento_id))
        if crudo is not None:
            try:
                return [UbicacionCita(**u) for u in json.loads(crudo)]
            except Exception as e:
                logger.warning("cache_ubicaciones_corrupto", doc_id=documento_id, error=str(e))

        ruta_pdf = storage_service.obtener_local(_objeto_pdf(documento_id))
        if ruta_pdf is None:
            return None

        citas = self._leer_citas(documento_id)
        ubicaciones = self._localizar(str(ruta_pdf), citas)

        storage_service.subir_texto(
            _objeto_cache(documento_id),
            json.dumps([u.model_dump() for u in ubicaciones], ensure_ascii=False),
        )
        return ubicaciones

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
        ubicaciones = self.obtener_ubicaciones(documento_id)
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

    def _leer_citas(self, documento_id: str) -> list[tuple[str, str]]:
        query = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
        RETURN c.id AS cita_id, c.texto AS texto_cita
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            return [
                (rec["cita_id"], rec["texto_cita"] or "")
                for rec in session.run(query, doc_id=documento_id)
            ]

    def _localizar(self, ruta_pdf: str, citas: list[tuple[str, str]]) -> list[UbicacionCita]:
        import fitz

        ubicaciones: list[UbicacionCita] = []
        with fitz.open(ruta_pdf) as doc:
            for cita_id, texto_cita in citas:
                ubicaciones.append(self._localizar_cita(doc, cita_id, texto_cita))

        localizadas = sum(1 for u in ubicaciones if u.pagina_real is not None)
        logger.info(
            "citas_localizadas",
            total=len(ubicaciones),
            localizadas=localizadas,
            no_localizadas=len(ubicaciones) - localizadas,
        )
        return ubicaciones

    def _localizar_cita(self, doc, cita_id: str, texto_cita: str) -> UbicacionCita:
        """Primer match gana: se prueba cada variante en todas las páginas."""
        if texto_cita.strip():
            for variante in _variantes_busqueda(texto_cita):
                for numero, page in enumerate(doc, start=1):
                    hits = page.search_for(variante)
                    if hits:
                        rects = [
                            RectResaltado(
                                pagina=numero,
                                x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1,
                                ancho_pagina=page.rect.width,
                                alto_pagina=page.rect.height,
                            )
                            for r in hits
                        ]
                        return UbicacionCita(
                            cita_id=cita_id,
                            texto_cita=texto_cita,
                            pagina_real=numero,
                            rects=rects,
                        )

        return UbicacionCita(cita_id=cita_id, texto_cita=texto_cita, pagina_real=None, rects=[])


# Singleton
localizacion_service = LocalizacionService()

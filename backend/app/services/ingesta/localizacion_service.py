"""
Localización exacta de citas dentro del PDF original (revisión humana).

Para cada cita del documento (leída de Neo4j) busca su texto en el PDF y
devuelve la página real (1-based) más los rectángulos donde aparece, con las
dimensiones de página para que el frontend escale el overlay de resaltado.

La búsqueda NO usa `page.search_for`: esa función no cruza saltos de línea de
forma fiable y tanto la cita como su contexto los cruzan constantemente en un
PDF justificado, que es de donde venían los resaltados que caían en el lugar
equivocado o en ninguno. En su lugar cada página se indexa una vez como texto
corrido con el rect de cada palabra (_indexar_pagina) y se busca ahí.

La misma cita "(Autor, año)" puede aparecer varias veces, así que primero se
busca por CONTEXTO: la cola del fragmento_oracion que termina en la cita, que
es (casi) única. Cada ubicación informa en `metodo` cómo se resolvió
—"contexto", "exacta", "ambigua", "no_hallada"— para que la revisión humana
sepa cuándo el resaltado es certero y cuándo es solo el mejor candidato.

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

_RE_ESPACIOS = re.compile(r"\s+")

logger = structlog.get_logger(__name__)
settings = get_settings()


# Versión del formato/algoritmo del cache de ubicaciones. Se incrementa al
# cambiar la localización para recalcular caches viejos (p. ej. los que
# asignaban todas las citas duplicadas a la primera ocurrencia).
_VERSION_CACHE = 3


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


# ── Índice de página: texto plano con un rectángulo por carácter ─────────────
# `page.search_for` no cruza saltos de línea de forma fiable, y una cita o su
# contexto casi siempre los cruza en un PDF justificado. Por eso cada página se
# indexa una vez como texto corrido —palabras unidas por un espacio, guiones de
# corte pegados— guardando el rect de cada palabra. Buscar pasa a ser un `find`
# sobre ese texto, insensible al layout, y el span encontrado se traduce de
# vuelta a rectángulos para el resaltado.

# Canonicalización 1:1 en longitud: no puede desplazar los offsets.
_CANON = str.maketrans({
    "’": "'", "‘": "'", "“": '"', "”": '"',
    # El guion se neutraliza a espacio en AMBOS lados de la comparación: el
    # markdown pierde el guion de "Johnson-Laird" cortado entre renglones
    # mientras el PDF lo conserva. Al aplicarse igual a aguja y página, la
    # equivalencia se mantiene sin desplazar offsets.
    "-": " ", "–": " ", "—": " ", "‑": " ", "−": " ",
    "\u00a0": " ", "\u202f": " ", "\u2009": " ",
})


def _canon(texto: str) -> str:
    """Minúsculas y variantes tipográficas unificadas, sin cambiar longitudes."""
    return texto.translate(_CANON).lower()


class _PaginaIndexada:
    """Texto corrido de una página + el rect de cada tramo de caracteres."""

    __slots__ = ("numero", "texto", "spans", "ancho", "alto")

    def __init__(self, numero, texto, spans, ancho, alto):
        self.numero = numero      # 1-based
        self.texto = texto        # ya canonicalizado
        self.spans = spans        # [(ini_char, fin_char, rect)] en orden
        self.ancho = ancho
        self.alto = alto


def _indexar_pagina(page, numero: int) -> _PaginaIndexada:
    palabras = page.get_text("words")
    # Orden de lectura: bloque, línea, palabra.
    palabras.sort(key=lambda w: (w[5], w[6], w[7]))

    partes: list[str] = []
    spans: list[tuple[int, int, object]] = []
    pos = 0
    for x0, y0, x1, y1, palabra, *_ in palabras:
        if partes:
            # "Johnson-" + "Laird" → "Johnson-Laird": el guion de corte de
            # renglón no lleva espacio, igual que en app.core.texto.aplanar.
            if not partes[-1].endswith("-"):
                partes.append(" ")
                pos += 1
        partes.append(palabra)
        spans.append((pos, pos + len(palabra), (x0, y0, x1, y1)))
        pos += len(palabra)

    return _PaginaIndexada(
        numero, _canon("".join(partes)), spans, page.rect.width, page.rect.height
    )


def _rects_de_span(pagina: _PaginaIndexada, inicio: int, fin: int) -> list[RectResaltado]:
    """Rectángulos de las palabras que caen dentro de [inicio, fin), por renglón."""
    tocados = [r for ini, f, r in pagina.spans if ini < fin and f > inicio]
    if not tocados:
        return []

    # Agrupar por renglón: mismas coordenadas verticales ⇒ un solo rect ancho.
    renglones: list[list[tuple]] = []
    for rect in tocados:
        for grupo in renglones:
            if abs(grupo[0][1] - rect[1]) < 3 and abs(grupo[0][3] - rect[3]) < 3:
                grupo.append(rect)
                break
        else:
            renglones.append([rect])

    return [
        RectResaltado(
            pagina=pagina.numero,
            x0=min(r[0] for r in grupo),
            y0=min(r[1] for r in grupo),
            x1=max(r[2] for r in grupo),
            y1=max(r[3] for r in grupo),
            ancho_pagina=pagina.ancho,
            alto_pagina=pagina.alto,
        )
        for grupo in renglones
    ]


def _buscar_en_documento(
    paginas: list[_PaginaIndexada], aguja: str
) -> list[tuple[_PaginaIndexada, int, int]]:
    """Todas las ocurrencias de `aguja` en el documento, en orden de lectura."""
    aguja = _canon(_RE_ESPACIOS.sub(" ", aguja).strip())
    if len(aguja) < 3:
        return []
    hallazgos = []
    for pagina in paginas:
        desde = 0
        while True:
            i = pagina.texto.find(aguja, desde)
            if i == -1:
                break
            hallazgos.append((pagina, i, i + len(aguja)))
            desde = i + 1
    return hallazgos

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
               c.texto_original AS texto_original,
               c.fragmento AS fragmento, c.pagina AS pagina
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            return [
                {
                    "cita_id": rec["cita_id"],
                    "texto_cita": rec["texto_cita"] or "",
                    # Lo que literalmente hay en el PDF: en un grupo
                    # "(A, 2020; B, 2019)" es el paréntesis entero.
                    "texto_original": rec["texto_original"] or rec["texto_cita"] or "",
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
            paginas = [_indexar_pagina(page, n) for n, page in enumerate(doc, start=1)]
            pagina_referencias = self._pagina_referencias(doc)

        # Las citas se procesan en orden de página estimada para que las
        # ocurrencias de un mismo texto se repartan en orden de aparición.
        orden = sorted(range(len(citas)), key=lambda i: citas[i]["pagina"])
        cache_ocurrencias: dict[str, list] = {}
        usadas: dict[str, int] = {}
        resultados: dict[int, UbicacionCita] = {}
        for i in orden:
            resultados[i] = self._localizar_cita(
                paginas, citas[i], cache_ocurrencias, usadas
            )
        ubicaciones = [resultados[i] for i in range(len(citas))]

        conteo: dict[str, int] = {}
        for u in ubicaciones:
            conteo[u.metodo] = conteo.get(u.metodo, 0) + 1
        logger.info(
            "citas_localizadas",
            total=len(ubicaciones),
            por_contexto=conteo.get("contexto", 0),
            exactas=conteo.get("exacta", 0),
            ambiguas=conteo.get("ambigua", 0),
            no_halladas=conteo.get("no_hallada", 0),
            pagina_referencias=pagina_referencias,
        )
        return ubicaciones, pagina_referencias

    # Contexto que se antepone a la cita para desambiguarla, y mínimo de
    # caracteres propios que debe aportar para considerarse distintivo.
    _CONTEXTO_CHARS = 70
    _MIN_CONTEXTO_EXTRA = 12

    def _localizar_cita(
        self,
        paginas: list["_PaginaIndexada"],
        cita: dict,
        cache: dict[str, list],
        usadas: dict[str, int],
    ) -> UbicacionCita:
        """
        Resuelve dónde está la cita en el PDF, en tres intentos:

        1. Por contexto: la cola de la oración que termina en la cita es casi
           única en el documento, así que distingue entre varias apariciones
           del mismo "(Autor, año)".
        2. Por texto literal: si aparece UNA sola vez, no hay ambigüedad.
        3. Si aparece varias veces y no hubo contexto, se reparten en orden de
           lectura, pero el resultado queda marcado como "ambigua" para que la
           revisión humana sepa que el resaltado puede no ser la ocurrencia
           exacta. Antes esto se devolvía como si fuera certero.
        """
        texto_cita = (cita.get("texto_cita") or "").strip()
        original = (cita.get("texto_original") or texto_cita).strip()

        def resultado(metodo, pagina=None, ini=0, fin=0) -> UbicacionCita:
            return UbicacionCita(
                cita_id=cita["cita_id"],
                texto_cita=cita.get("texto_cita", ""),
                pagina_real=pagina.numero if pagina else None,
                rects=_rects_de_span(pagina, ini, fin) if pagina else [],
                metodo=metodo,
            )

        if not original:
            return resultado("no_hallada")

        # 1) Contexto
        fragmento = _RE_ESPACIOS.sub(" ", cita.get("fragmento") or "").strip()
        pos = fragmento.rfind(original)
        if pos != -1:
            contexto = fragmento[max(0, pos - self._CONTEXTO_CHARS):pos + len(original)].strip()
            if len(contexto) >= len(original) + self._MIN_CONTEXTO_EXTRA:
                hallazgos = self._ocurrencias(paginas, contexto, cache)
                if hallazgos:
                    pagina, ini, fin = hallazgos[0]
                    # Resaltar solo la cita, no el contexto entero.
                    aguja = _canon(_RE_ESPACIOS.sub(" ", original).strip())
                    rel = pagina.texto.find(aguja, ini, fin)
                    if rel != -1:
                        ini, fin = rel, rel + len(aguja)
                    return resultado("contexto", pagina, ini, fin)

        # 2 y 3) Texto literal y variantes
        for aguja in [original, *_variantes_busqueda(texto_cita)]:
            hallazgos = self._ocurrencias(paginas, aguja, cache)
            if not hallazgos:
                continue
            if len(hallazgos) == 1:
                pagina, ini, fin = hallazgos[0]
                return resultado("exacta", pagina, ini, fin)
            indice = min(usadas.get(aguja, 0), len(hallazgos) - 1)
            usadas[aguja] = indice + 1
            pagina, ini, fin = hallazgos[indice]
            logger.debug(
                "cita_ambigua", cita=texto_cita, ocurrencias=len(hallazgos), elegida=indice
            )
            return resultado("ambigua", pagina, ini, fin)

        return resultado("no_hallada")

    @staticmethod
    def _ocurrencias(
        paginas: list["_PaginaIndexada"], aguja: str, cache: dict[str, list]
    ) -> list[tuple]:
        if aguja not in cache:
            cache[aguja] = _buscar_en_documento(paginas, aguja)
        return cache[aguja]

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

# Singleton
localizacion_service = LocalizacionService()

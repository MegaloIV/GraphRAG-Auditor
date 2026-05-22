"""
Servicio de embeddings con sentence-transformers + Supabase (pgvector).
Genera embeddings del texto de cada paper y los almacena en Supabase
para búsqueda semántica en EP-003.

Modelo: all-MiniLM-L6-v2 (gratuito, corre local, 80 MB)
- 384 dimensiones
- Muy rápido
- Bueno para textos académicos en inglés
"""
import re
import structlog
from typing import Optional

from sentence_transformers import SentenceTransformer

from app.services.vectorstore.supabase_service import supabase_vector_service

logger = structlog.get_logger(__name__)

MODELO_EMBEDDING = "all-MiniLM-L6-v2"


_RE_PAGE_MARKER = re.compile(
    r"(?:^|\n)(?:Page\s+\d+|---\s*Page\s+\d+\s*---|\[Page\s+\d+\])\s*(?:\n|$)",
    re.IGNORECASE,
)

# Encabezado explícito de sección de referencias (línea sola, # o **)
_RE_REF_HEADER = re.compile(
    r'^(?:#{1,6}\s*|\*\*)?(?:References|Bibliography|Works\s+Cited|Referencias|Literatura\s+citada)(?:\*\*)?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

# Vancouver estricto: "1. Apellido..." (mayúscula + minúscula obligatorias)
_RE_VANCOUVER_LINE = re.compile(r'^\d+\.\s+[A-Z][a-z]+')

# Encabezado de sección final (línea sola, # o **)
_RE_SECTION_FINAL = re.compile(
    r'^(?:#{1,6}\s*|\*\*)?(?:Competing\s+interests|Acknowledgements|Declarations|Funding|Author\s+contributions)(?:\*\*)?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

MAX_CHUNK_CHARS = 1500

_RE_URL = re.compile(r'https?://\S+', re.IGNORECASE)
_RE_EMAIL = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
_RE_ORCID = re.compile(r'orcid\.org/', re.IGNORECASE)
_RE_INSTITUCION = re.compile(
    r'\b(?:University|Universidad|Institute|College|Library|Hospital)\b',
    re.IGNORECASE,
)
_RE_MARKDOWN_CHARS = re.compile(r'[#*_`\[\]()>~|]')

_FRASES_BOILERPLATE = [
    "publisher's note",
    "springer nature remains neutral",
    "the authors declare",
    "conflict of interest",
    "acknowledgements",
    "funding",
    "data availability",
]


def _es_chunk_basura(chunk: str) -> bool:
    """
    Devuelve True si el chunk es ruido descartable antes de indexar.

    Condiciones (cualquiera es suficiente):
    - Más del 40 % del texto son URLs.
    - Contiene frases de boilerplate editorial/legal.
    - Menos de 100 caracteres de contenido real (sin URLs ni espacios).
    """
    urls = _RE_URL.findall(chunk)
    chars_url = sum(len(u) for u in urls)
    if len(chunk) > 0 and chars_url / len(chunk) > 0.40:
        return True

    chunk_lower = chunk.lower()
    if any(frase in chunk_lower for frase in _FRASES_BOILERPLATE):
        return True

    sin_urls = _RE_URL.sub("", chunk)
    chars_reales = len(sin_urls.replace(" ", "").replace("\n", "").replace("\t", ""))
    if chars_reales < 100:
        return True

    return False


def _truncar_referencias_paper(texto: str, doi: str = "") -> str:
    """
    Elimina la sección de referencias bibliográficas antes de indexar.

    Busca la primera señal después del 50 % del texto:
    1. Línea sola con encabezado de referencias (texto plano, # o **).
    2. 3+ líneas consecutivas con patrón Vancouver: "N. Apellido...".
    3. Línea sola con encabezado de sección final (Funding, Acknowledgements…).

    Si no se detecta ninguna señal, devuelve el texto intacto.
    """
    n = len(texto)
    if n == 0:
        return texto

    mitad = n // 2
    zona = texto[mitad:]

    candidatos: dict[str, int] = {}

    # Señal 1: encabezado explícito de referencias
    m = _RE_REF_HEADER.search(zona)
    if m:
        candidatos["encabezado_referencias"] = m.start()

    # Señal 3: encabezado de sección final
    m = _RE_SECTION_FINAL.search(zona)
    if m:
        candidatos["seccion_final"] = m.start()

    # Señal 2: 3+ líneas consecutivas con patrón Vancouver
    lineas = zona.splitlines(keepends=True)
    consecutivas = 0
    pos_inicio_bloque: int | None = None
    pos_actual = 0
    for linea in lineas:
        if _RE_VANCOUVER_LINE.match(linea):
            if consecutivas == 0:
                pos_inicio_bloque = pos_actual
            consecutivas += 1
            if consecutivas >= 3:
                candidatos["patron_vancouver"] = pos_inicio_bloque  # type: ignore[assignment]
                break
        else:
            consecutivas = 0
            pos_inicio_bloque = None
        pos_actual += len(linea)

    if not candidatos:
        return texto

    metodo, pos_zona = min(candidatos.items(), key=lambda x: x[1])
    pos = mitad + pos_zona
    logger.debug(
        "referencias_paper_truncadas",
        doi=doi,
        metodo=metodo,
        pos_inicio=pos,
        chars_descartados=n - pos,
    )
    return texto[:pos].rstrip()


def _es_chunk_portada(chunk: str, chunk_index: int, doi: str) -> bool:
    """
    Devuelve True si el chunk parece ser una portada (título/autores/afiliaciones).

    Suma señales; descarta si se cumplen 2 o más:
    1. 2+ emails en el chunk.
    2. 2+ ORCIDs (orcid.org/).
    3. 2+ palabras de institución académica (University, College, Library…).
    4. Menos de 300 caracteres reales (sin URLs, emails ni markdown).
    """
    señales = 0

    if len(_RE_EMAIL.findall(chunk)) >= 2:
        señales += 1

    if len(_RE_ORCID.findall(chunk)) >= 2:
        señales += 1

    if len(_RE_INSTITUCION.findall(chunk)) >= 2:
        señales += 1

    sin_ruido = _RE_MARKDOWN_CHARS.sub("", _RE_EMAIL.sub("", _RE_URL.sub("", chunk)))
    chars_reales = len(sin_ruido.replace(" ", "").replace("\n", "").replace("\t", ""))
    if chars_reales < 300:
        señales += 1

    if señales >= 2:
        logger.debug(
            "chunk_descartado_portada",
            chunk_index=chunk_index,
            doi=doi,
            señales_detectadas=señales,
        )
        return True

    return False


def _dividir_en_chunks(texto: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """
    Divide texto en chunks por marcadores de página o por tamaño.

    Prioridad:
    1. Form-feed (\f) — marcador estándar de salto de página PDF
    2. Patrones textuales: "Page N", "--- Page N ---", "[Page N]"
    3. Bloques de max_chars caracteres cortando en párrafos completos
    """
    if "\f" in texto:
        chunks = [c.strip() for c in texto.split("\f") if c.strip()]
        if chunks:
            return chunks

    partes = _RE_PAGE_MARKER.split(texto)
    partes = [p.strip() for p in partes if p.strip()]
    if len(partes) > 1:
        return partes

    # Sin marcadores → trocear por tamaño respetando párrafos
    chunks: list[str] = []
    restante = texto.strip()
    while restante:
        if len(restante) <= max_chars:
            chunks.append(restante)
            break

        ventana = restante[:max_chars]
        mitad = max_chars // 2

        corte = ventana.rfind("\n\n")
        if corte < mitad:
            corte = ventana.rfind("\n")
        if corte < mitad:
            punto = ventana.rfind(". ")
            corte = punto + 1 if punto >= mitad else max_chars

        chunks.append(restante[:corte].strip())
        restante = restante[corte:].strip()

    return [c for c in chunks if c]


class EmbeddingService:

    def __init__(self):
        self._modelo: SentenceTransformer | None = None

    @property
    def modelo(self) -> SentenceTransformer:
        """Carga el modelo de embeddings solo cuando se necesita."""
        if self._modelo is None:
            logger.info("cargando_modelo_embeddings", modelo=MODELO_EMBEDDING)
            self._modelo = SentenceTransformer(MODELO_EMBEDDING)
            logger.info("modelo_embeddings_listo")
        return self._modelo

    def indexar_paper(
        self,
        doi: str,
        texto: str,
        metadata: dict,
    ) -> bool:
        """
        Limpia, divide en chunks e indexa el texto del paper en Supabase.

        Pipeline:
          1. Truncar sección de referencias al final del paper.
          2. Dividir en chunks por página o por tamaño.
          3. Descartar chunks basura (boilerplate, exceso de URLs, muy cortos).
          4. Borrar chunks existentes del DOI en Supabase.
          5. Generar embeddings e insertar chunks limpios.

        Args:
            doi:      DOI del paper (se usa como clave única).
            texto:    Texto completo del paper.
            metadata: Título, año, nivel_confianza, referencia_id, etc.

        Returns:
            True si al menos un chunk se indexó correctamente.
        """
        if not texto or not texto.strip():
            logger.warning("indexar_paper_texto_vacio", doi=doi)
            return False

        try:
            doi_normalizado = doi.replace("/", "_").replace(".", "_")

            texto_limpio = _truncar_referencias_paper(texto, doi=doi)
            chunks_raw = _dividir_en_chunks(texto_limpio)
            chunks = [
                c for idx, c in enumerate(chunks_raw)
                if not _es_chunk_portada(c, idx, doi) and not _es_chunk_basura(c)
            ]
            descartados = len(chunks_raw) - len(chunks)

            if descartados:
                logger.info(
                    "chunks_basura_descartados",
                    doi=doi,
                    descartados=descartados,
                    total_raw=len(chunks_raw),
                )

            if not chunks:
                logger.warning("indexar_paper_sin_chunks_utiles", doi=doi)
                return False

            logger.info("paper_dividido_en_chunks", doi=doi, total_chunks=len(chunks))

            # Generar embeddings y construir registros para Supabase
            registros = []
            for idx, chunk in enumerate(chunks):
                embedding = self.modelo.encode(chunk).tolist()
                registros.append({
                    "id":              f"{doi_normalizado}_chunk_{idx}",
                    "doi":             doi,
                    "doi_normalizado": doi_normalizado,
                    "titulo":          metadata.get("titulo"),
                    "anio":            metadata.get("anio"),
                    "pagina":          idx + 1,
                    "chunk_index":     idx,
                    "nivel_confianza": metadata.get("nivel_confianza"),
                    "referencia_id":   metadata.get("referencia_id"),
                    "contenido":       chunk,
                    "embedding":       embedding,
                })

            supabase_vector_service.eliminar_chunks_por_doi(doi_normalizado)
            ok = supabase_vector_service.indexar_chunks(registros)

            if ok:
                logger.info(
                    "paper_indexado",
                    doi=doi,
                    chunks=len(registros),
                    nivel_confianza=metadata.get("nivel_confianza"),
                )
            return ok

        except Exception as e:
            logger.error("error_indexando_paper", doi=doi, error=str(e))
            return False

    def buscar_similares(
        self,
        texto_consulta: str,
        n_resultados: int = 3,
        filtro_doi: Optional[str] = None,
    ) -> list[dict]:
        """
        Busca chunks similares a texto_consulta en Supabase.
        Devuelve el mismo formato que antes para mantener compatibilidad:
          [{"texto": ..., "metadata": {"pagina": ..., "chunk_index": ..., "doi": ...},
            "similitud": float}]
        """
        try:
            embedding = self.modelo.encode(texto_consulta).tolist()
            doi_normalizado = filtro_doi.replace("/", "_").replace(".", "_") if filtro_doi else None

            filas = supabase_vector_service.buscar_similares(
                embedding=embedding,
                doi_normalizado=doi_normalizado,
                top_k=n_resultados,
            )

            items = []
            for f in filas:
                items.append({
                    "texto": f["contenido"],
                    "metadata": {
                        "pagina":      f.get("pagina"),
                        "chunk_index": f.get("chunk_index"),
                        "doi":         f.get("doi"),
                    },
                    "similitud": round(float(f["similitud"]), 4),
                })

            logger.info(
                "busqueda_embeddings",
                consulta=texto_consulta[:50],
                resultados=len(items),
            )
            return items

        except Exception as e:
            logger.error("error_busqueda_embeddings", error=str(e))
            return []

    def paper_ya_indexado(self, doi: str) -> bool:
        """Verifica si un paper ya fue indexado en Supabase."""
        doi_normalizado = doi.replace("/", "_").replace(".", "_")
        return supabase_vector_service.paper_ya_indexado(doi_normalizado)

    def total_papers_indexados(self) -> int:
        """Retorna el total de chunks en Supabase."""
        return supabase_vector_service.total_chunks()


# Singleton
embedding_service = EmbeddingService()

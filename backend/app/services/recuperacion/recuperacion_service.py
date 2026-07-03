"""
EP-003: Motor de Recuperación Híbrida (GraphRAG)

Flujo por cita:
  1. Neo4j: cita → CITA_A → referencia → DOI
  2. Supabase/pgvector: buscar chunks del paper de ese DOI similares al texto de la cita
  3. Retornar: fragmento más similar + nodos del grafo vinculados

HU-007: indicador de preparación (total chunks indexados para el doc)
HU-008: consulta libre de una cita específica
HU-009: ruta de evidencia = fragmento textual + nodos del grafo
"""
import structlog
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service
from app.services.externo.embedding_service import embedding_service
from app.services.vectorstore.supabase_service import supabase_vector_service

settings = get_settings()

logger = structlog.get_logger(__name__)


# ── Modelos de respuesta internos ────────────────────────────────────────────

@dataclass
class NodoGrafo:
    tipo: str          # "Referencia" | "Autor" | "Cita"
    nombre: str
    relacion: str      # descripción de la relación con la consulta
    propiedades: dict = field(default_factory=dict)


@dataclass
class ResultadoRecuperacion:
    cita_id: str
    texto_cita: str
    fragmento_relevante: str       # chunk de Supabase/pgvector más similar
    similitud: float               # 0..1
    nodos_grafo: list[NodoGrafo]   # ruta de evidencia HU-009
    doi_referencia: Optional[str]
    referencia_id: Optional[str]
    metodo: str                    # "hibrido" | "solo_grafo" | "solo_vectorial" | "no_encontrado"
    pagina_paper: Optional[int] = None   # página del chunk recuperado (None si indexado sin chunks)
    chunk_index: Optional[int] = None    # índice 0-based del chunk recuperado


@dataclass
class EstadoMotor:
    listo: bool
    total_chunks: int
    total_citas_vinculadas: int
    total_referencias: int
    mensaje: str


# ── Queries Cypher ────────────────────────────────────────────────────────────

_Q_RUTA_EVIDENCIA = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita {id: $cita_id})
OPTIONAL MATCH (c)-[rel:CITA_A]->(r:Referencia)
OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
RETURN
  c.id       AS cita_id,
  c.texto    AS texto_cita,
  r.id       AS ref_id,
  r.titulo   AS ref_titulo,
  r.anio     AS ref_anio,
  r.doi_verificado AS doi,
  r.doi      AS doi_raw,
  collect(DISTINCT a.nombre) AS autores,
  rel.confianza AS confianza_vinculo
"""

_Q_CITAS_VINCULADAS_COUNT = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)-[:CITA_A]->()
RETURN count(c) AS total
"""

_Q_REFERENCIAS_COUNT = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
RETURN count(r) AS total
"""

_Q_TODAS_CITAS = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
RETURN c.id AS cita_id, c.texto AS texto_cita
"""


class RecuperacionService:

    # ── HU-007: Estado del motor ─────────────────────────────────────────

    def estado_motor(self, documento_id: str) -> EstadoMotor:
        """
        Verifica que el motor está listo para auditar el documento:
        - Hay chunks en Supabase/pgvector (al menos para alguna referencia del doc)
        - Hay citas vinculadas a referencias en Neo4j
        """
        try:
            total_chunks = supabase_vector_service.total_chunks()

            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                vinculadas = session.run(
                    _Q_CITAS_VINCULADAS_COUNT, doc_id=documento_id
                ).single()["total"]

                total_refs = session.run(
                    _Q_REFERENCIAS_COUNT, doc_id=documento_id
                ).single()["total"]

            listo = total_chunks > 0 and vinculadas > 0

            if not listo:
                if total_chunks == 0:
                    mensaje = (
                        "No hay fragmentos indexados. "
                        "Verifica que la verificación de referencias completó correctamente."
                    )
                else:
                    mensaje = (
                        f"No se encontraron citas vinculadas a referencias. "
                        f"El grafo tiene {total_refs} referencias pero 0 vínculos."
                    )
            else:
                mensaje = (
                    f"Motor listo. {total_chunks} fragmentos indexados. "
                    f"{vinculadas} citas vinculadas a sus referencias."
                )

            return EstadoMotor(
                listo=listo,
                total_chunks=total_chunks,
                total_citas_vinculadas=vinculadas,
                total_referencias=total_refs,
                mensaje=mensaje,
            )

        except Exception as e:
            logger.error("error_estado_motor", doc_id=documento_id, error=str(e))
            return EstadoMotor(
                listo=False,
                total_chunks=0,
                total_citas_vinculadas=0,
                total_referencias=0,
                mensaje=f"Error al verificar el estado del motor: {str(e)}",
            )

    # ── HU-008: Consulta manual de una cita ─────────────────────────────

    def consultar_cita(
        self,
        documento_id: str,
        cita_id: str,
        n_fragmentos: int = 3,
        fragmento_oracion: str = "",
    ) -> ResultadoRecuperacion:
        """
        Recupera la evidencia para una cita específica.
        Combina traversal de grafo + búsqueda vectorial en Supabase/pgvector.
        """
        # Paso 1: obtener la ruta del grafo (Neo4j)
        ruta = self._obtener_ruta_grafo(documento_id, cita_id)

        if not ruta:
            return ResultadoRecuperacion(
                cita_id=cita_id,
                texto_cita="",
                fragmento_relevante="",
                similitud=0.0,
                nodos_grafo=[],
                doi_referencia=None,
                referencia_id=None,
                metodo="no_encontrado",
            )

        texto_cita    = ruta["texto_cita"] or ""
        ref_id        = ruta["ref_id"]
        doi           = ruta["doi"] or ruta["doi_raw"]
        autores       = ruta["autores"] or []
        ref_titulo    = ruta["ref_titulo"] or ""
        ref_anio      = ruta["ref_anio"]
        confianza     = ruta["confianza_vinculo"]

        # Paso 2: construir nodos de evidencia del grafo (HU-009)
        nodos = self._construir_nodos_grafo(
            ref_id=ref_id,
            ref_titulo=ref_titulo,
            ref_anio=ref_anio,
            autores=autores,
            confianza=confianza,
        )

        # Paso 3: búsqueda vectorial en Supabase (solo si hay DOI)
        if doi and texto_cita:
            doi_normalizado = doi.replace("/", "_").replace(".", "_")
            query = fragmento_oracion.strip() or texto_cita
            embedding_vec = embedding_service.generar_embedding(query)
            fragmentos = supabase_vector_service.buscar_similares(
                embedding=embedding_vec,
                doi_normalizado=doi_normalizado,
                top_k=n_fragmentos,
            )

            if fragmentos:
                mejor = fragmentos[0]
                pagina_paper = mejor.get("pagina")
                chunk_index  = mejor.get("chunk_index")

                contenido_concat = "\n\n---\n\n".join(
                    f["contenido"] for f in fragmentos
                )[:3000]

                logger.debug(
                    "chunk_recuperado",
                    cita_id=cita_id,
                    doi=doi,
                    pagina_paper=pagina_paper,
                    chunk_index=chunk_index,
                    similitud=mejor["similitud"],
                    chunks_concatenados=len(fragmentos),
                )

                return ResultadoRecuperacion(
                    cita_id=cita_id,
                    texto_cita=texto_cita,
                    fragmento_relevante=contenido_concat,
                    similitud=float(mejor["similitud"]),
                    nodos_grafo=nodos,
                    doi_referencia=doi,
                    referencia_id=ref_id,
                    metodo="hibrido",
                    pagina_paper=pagina_paper,
                    chunk_index=chunk_index,
                )

        # Fallback: solo grafo (no hay chunks de ese paper en Supabase/pgvector)
        if ref_id:
            return ResultadoRecuperacion(
                cita_id=cita_id,
                texto_cita=texto_cita,
                fragmento_relevante="",
                similitud=0.0,
                nodos_grafo=nodos,
                doi_referencia=doi,
                referencia_id=ref_id,
                metodo="solo_grafo",
            )

        return ResultadoRecuperacion(
            cita_id=cita_id,
            texto_cita=texto_cita,
            fragmento_relevante="",
            similitud=0.0,
            nodos_grafo=[],
            doi_referencia=None,
            referencia_id=None,
            metodo="no_encontrado",
        )

    # ── HU-008 variante: consulta libre por texto ─────────────────────────

    def consultar_texto_libre(
        self,
        documento_id: str,
        texto_consulta: str,
        n_resultados: int = 3,
    ) -> list[ResultadoRecuperacion]:
        """
        Campo de búsqueda libre: dado un texto, busca las citas
        del documento más relacionadas y su evidencia.
        """
        # Obtener todas las citas del documento
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            citas_raw = list(session.run(_Q_TODAS_CITAS, doc_id=documento_id))

        if not citas_raw:
            return []

        # Buscar en Supabase sin filtro de DOI (búsqueda global)
        embedding_vec = embedding_service.generar_embedding(texto_consulta)
        fragmentos_globales = supabase_vector_service.buscar_similares(
            embedding=embedding_vec,
            doi_normalizado=None,
            top_k=n_resultados,
        )

        if not fragmentos_globales:
            return []

        # Cruzar fragmentos con citas del documento por DOI
        resultados = []
        dois_vistos = set()

        for fragmento in fragmentos_globales:
            doi_frag = fragmento.get("doi")
            if doi_frag in dois_vistos:
                continue
            dois_vistos.add(doi_frag)

            # Buscar cita del doc que tenga este DOI vinculado
            cita_match = self._cita_por_doi(documento_id, doi_frag)
            if cita_match:
                resultado = self.consultar_cita(documento_id, cita_match)
                resultado.fragmento_relevante = fragmento["contenido"]
                resultado.similitud = float(fragmento["similitud"])
                resultados.append(resultado)
            else:
                # Crear resultado solo vectorial sin cita vinculada
                resultados.append(ResultadoRecuperacion(
                    cita_id="",
                    texto_cita=texto_consulta,
                    fragmento_relevante=fragmento["contenido"],
                    similitud=float(fragmento["similitud"]),
                    nodos_grafo=[],
                    doi_referencia=doi_frag,
                    referencia_id=None,
                    metodo="solo_vectorial",
                    pagina_paper=fragmento.get("pagina"),
                    chunk_index=fragmento.get("chunk_index"),
                ))

        return resultados

    # ── Internos ──────────────────────────────────────────────────────────

    def _obtener_ruta_grafo(self, documento_id: str, cita_id: str) -> dict | None:
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                resultado = session.run(
                    _Q_RUTA_EVIDENCIA,
                    doc_id=documento_id,
                    cita_id=cita_id,
                ).single()
            return dict(resultado) if resultado else None
        except Exception as e:
            logger.error("error_ruta_grafo", cita_id=cita_id, error=str(e))
            return None

    @staticmethod
    def _construir_nodos_grafo(
        ref_id: str | None,
        ref_titulo: str,
        ref_anio: int | None,
        autores: list[str],
        confianza: float | None,
    ) -> list[NodoGrafo]:
        nodos = []

        if ref_id:
            anio_str = f" ({ref_anio})" if ref_anio else ""
            nodos.append(NodoGrafo(
                tipo="Referencia",
                nombre=ref_titulo or "Sin título",
                relacion=f"Referencia citada{anio_str}",
                propiedades={
                    "id": ref_id,
                    "anio": ref_anio,
                    "confianza_vinculo": confianza,
                },
            ))

        for autor in autores:
            nodos.append(NodoGrafo(
                tipo="Autor",
                nombre=autor,
                relacion="Autor de la referencia citada",
                propiedades={},
            ))

        return nodos

    def _cita_por_doi(self, documento_id: str, doi: str | None) -> str | None:
        """Retorna el cita_id de la primera cita del doc vinculada a ese DOI."""
        if not doi:
            return None
        query = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)-[:CITA_A]->(r:Referencia)
        WHERE r.doi_verificado = $doi OR r.doi = $doi
        RETURN c.id AS cita_id LIMIT 1
        """
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                resultado = session.run(query, doc_id=documento_id, doi=doi).single()
            return resultado["cita_id"] if resultado else None
        except Exception:
            return None


# Singleton
recuperacion_service = RecuperacionService()
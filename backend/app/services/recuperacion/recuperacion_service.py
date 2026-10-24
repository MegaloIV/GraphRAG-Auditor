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
import re
import structlog
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service
from app.services.externo.embedding_service import embedding_service
from app.services.llm.openai_client import llm_service
from app.services.vectorstore.supabase_service import supabase_vector_service

settings = get_settings()

logger = structlog.get_logger(__name__)


from app.core.texto import limpiar_fragmento  # noqa: E402 — un solo punto de limpieza


def _unir_sin_solape(a: str, b: str, max_solape: int = 300) -> str:
    """
    Une dos chunks contiguos eliminando el texto solapado (los sub-chunks se
    indexan con ~150 chars de solapamiento para no cortar ideas a la mitad).
    """
    tope = min(max_solape, len(a), len(b))
    for k in range(tope, 20, -1):
        if a.endswith(b[:k]):
            return a + b[k:]
    return a + " " + b


# Anclas léxicas: cifras con decimales y porcentajes de la afirmación
# ("6.7", "80 %"). Son idénticas en español e inglés, así que rescatan
# chunks que la similitud vectorial cross-idioma no rankea.
_RE_NUMEROS_ANCLA = re.compile(r"\b\d+[.,]\d+\b|\b\d+\s*%")

_SYSTEM_TRADUCCION = (
    "Traduce la consulta al inglés académico. "
    "Responde ÚNICAMENTE con la traducción, sin comillas ni comentarios."
)


def _terminos_ancla(texto: str) -> list[str]:
    terminos: list[str] = []
    for t in _RE_NUMEROS_ANCLA.findall(texto):
        # Los papers en inglés usan punto decimal: "6,7" → "6.7".
        t = t.strip().replace(",", ".")
        if t not in terminos:
            terminos.append(t)
    return terminos[:3]


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
    anclas_lexicas: int = 0              # cifras del claim halladas por término exacto
    # Sección del paper de donde salió la evidencia. "trabajos_relacionados"
    # significa que el pasaje es la fuente resumiendo trabajo ajeno.
    seccion_evidencia: str = ""
    # Cuando la evidencia NO viene del paper citado sino de un vecino del
    # grafo: qué referencia la aportó y por qué arista se llegó a ella.
    referencia_evidencia_id: Optional[str] = None
    titulo_evidencia: str = ""
    via_grafo: str = ""


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
CALL (r) {
  OPTIONAL MATCH (r)-[e:ESCRITO_POR]->(a:Autor)
  WITH a ORDER BY e.orden
  RETURN collect(a.nombre) AS autores
}
RETURN
  c.id       AS cita_id,
  c.texto    AS texto_cita,
  r.id       AS ref_id,
  r.titulo   AS ref_titulo,
  r.anio     AS ref_anio,
  r.doi_verificado AS doi,
  r.doi      AS doi_raw,
  autores,
  c.consulta_en AS consulta_en,
  rel.confianza AS confianza_vinculo
"""

_Q_GUARDAR_CONSULTA_EN = """
MATCH (c:Cita {id: $cita_id}) SET c.consulta_en = $consulta_en
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


# ── Expansión por el grafo ───────────────────────────────────────────────────
# Cuando el paper citado no tiene ningún pasaje que sostenga la afirmación —o
# ni siquiera se pudo descargar— la búsqueda filtrada por DOI se queda sin
# nada y la cita muere en NO_INFO. Sobre tres tesis reales eso ocurrió 152
# veces teniendo la referencia perfectamente identificada.
#
# El grafo permite seguir buscando en los papers VECINOS de la bibliografía, y
# distinguir dos situaciones que hoy se confunden en el mismo NO_INFO:
#   - la afirmación está en otra fuente que el tesista también cita
#     (probable confusión de referencia), y
#   - la afirmación no está en ninguna.
#
# Lo que se encuentre así NO es una verificación: el pasaje viene de un paper
# distinto al citado. Se devuelve con metodo="vecino_grafo" para que la
# auditoría no lo confunda jamás con un respaldo.

_Q_VECINOS_POR_AUTOR = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(origen:Referencia {id: $ref_id})
MATCH (origen)-[:ESCRITO_POR]->(a:Autor)<-[:ESCRITO_POR]-(vecina:Referencia)
MATCH (d)-[:TIENE_REFERENCIA]->(vecina)
WHERE vecina.id <> origen.id AND coalesce(vecina.doi_verificado, '') <> ''
RETURN DISTINCT vecina.id AS ref_id, vecina.doi_verificado AS doi,
       vecina.titulo AS titulo, 'autor_compartido' AS via
LIMIT $limite
"""

# El paper citado cita a X, y X está en la bibliografía de la tesis: es el
# vecino más pertinente de todos, porque es la fuente primaria declarada.
_Q_VECINOS_POR_CITA_EXTERNA = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(origen:Referencia {id: $ref_id})
MATCH (origen)-[:CITA_EXTERNA]->(x:ReferenciaExterna)
WHERE coalesce(x.doi, '') <> ''
MATCH (d)-[:TIENE_REFERENCIA]->(vecina:Referencia)
WHERE toLower(coalesce(vecina.doi_verificado, vecina.doi, '')) = toLower(x.doi)
  AND vecina.id <> origen.id
RETURN DISTINCT vecina.id AS ref_id, vecina.doi_verificado AS doi,
       vecina.titulo AS titulo, 'citada_por_la_fuente' AS via
LIMIT $limite
"""

# Conceptos: los deja el módulo de coherencia. Si nunca se ejecutó, no hay
# nodos y la consulta devuelve vacío sin romper nada.
_Q_VECINOS_POR_CONCEPTO = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(origen:Referencia {id: $ref_id})
MATCH (e1:Evidencia {ref_id: origen.id})-[:MENCIONA]->(k:Concepto)<-[:MENCIONA]-(e2:Evidencia)
MATCH (d)-[:TIENE_REFERENCIA]->(vecina:Referencia {id: e2.ref_id})
WHERE vecina.id <> origen.id AND coalesce(vecina.doi_verificado, '') <> ''
RETURN DISTINCT vecina.id AS ref_id, vecina.doi_verificado AS doi,
       vecina.titulo AS titulo, 'concepto_compartido' AS via
LIMIT $limite
"""

_MAX_VECINOS = 6

# Umbral para aceptar evidencia de un paper distinto al citado. Es más
# exigente que el de la búsqueda normal: apuntar a la fuente equivocada
# confunde más que no decir nada.
_UMBRAL_VECINO = 0.62

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

        # Paso 3: búsqueda vectorial bilingüe + anclas léxicas (solo si hay DOI)
        if doi and texto_cita:
            doi_normalizado = doi.replace("/", "_").replace(".", "_")
            query = fragmento_oracion.strip() or texto_cita
            fragmentos, traduccion, anclas = self._buscar_fragmentos(
                query, doi_normalizado, n_fragmentos, traduccion=ruta.get("consulta_en")
            )
            if traduccion and traduccion != ruta.get("consulta_en"):
                self._guardar_consulta_en(cita_id, traduccion)

            if fragmentos:
                mejor = fragmentos[0]
                pagina_paper = mejor.get("pagina")
                chunk_index  = mejor.get("chunk_index")

                contenido_concat = self._coser_ventana(doi_normalizado, fragmentos)

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
                    anclas_lexicas=anclas,
                    seccion_evidencia=mejor.get("seccion") or "",
                )

        # El paper citado no dio nada (o no está indexado): se expande por el
        # grafo a los vecinos de la propia bibliografía. Lo que se encuentre
        # así NO verifica la cita —viene de otro paper— pero distingue "la
        # afirmación está en otra fuente tuya" de "no está en ninguna".
        if ref_id and texto_cita:
            hallazgo = self._buscar_en_vecinos(
                documento_id, ref_id,
                fragmento_oracion.strip() or texto_cita,
                ruta.get("consulta_en"), n_fragmentos,
            )
            if hallazgo:
                mejor, vecino = hallazgo
                return ResultadoRecuperacion(
                    cita_id=cita_id,
                    texto_cita=texto_cita,
                    fragmento_relevante=limpiar_fragmento(mejor["contenido"]),
                    similitud=float(mejor["similitud"]),
                    nodos_grafo=nodos,
                    doi_referencia=doi,
                    referencia_id=ref_id,
                    metodo="vecino_grafo",
                    pagina_paper=mejor.get("pagina"),
                    chunk_index=mejor.get("chunk_index"),
                    seccion_evidencia=mejor.get("seccion") or "",
                    referencia_evidencia_id=vecino["ref_id"],
                    titulo_evidencia=vecino.get("titulo") or "",
                    via_grafo=vecino.get("via") or "",
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

    def _buscar_fragmentos(
        self, query: str, doi_normalizado: str, top_k: int,
        traduccion: str | None = None,
    ) -> tuple[list[dict], str | None, int]:
        """
        Búsqueda en dos frentes para cerrar la brecha español→inglés:

        1. Vectorial bilingüe: la consulta se busca tal cual Y traducida al
           inglés (la tesis está en español, los papers casi siempre en
           inglés); los resultados se fusionan por mejor similitud.
        2. Anclas léxicas: los chunks que contienen las cifras de la
           afirmación ("6.7", "80%") se anexan como candidatos aunque el
           vector no los haya rankeado — el juez los recibe como pasajes
           adicionales.
        """
        consultas = [query]
        if traduccion is None:
            traduccion = self._traducir_a_ingles(query)
        if traduccion and traduccion.lower() != query.lower():
            consultas.append(traduccion)

        por_id: dict[str, dict] = {}
        for consulta in consultas:
            embedding_vec = embedding_service.generar_embedding(consulta)
            for f in supabase_vector_service.buscar_similares(
                embedding=embedding_vec,
                doi_normalizado=doi_normalizado,
                top_k=top_k,
            ):
                previo = por_id.get(f["id"])
                if previo is None or f["similitud"] > previo["similitud"]:
                    por_id[f["id"]] = f

        fragmentos = sorted(
            por_id.values(), key=lambda f: f["similitud"], reverse=True
        )[:top_k]

        ids = {f["id"] for f in fragmentos}
        anclas = 0
        for termino in _terminos_ancla(query):
            for f in supabase_vector_service.buscar_por_termino(
                doi_normalizado, termino, limit=1
            ):
                if f["id"] not in ids:
                    f["similitud"] = 0.0
                    fragmentos.append(f)
                    ids.add(f["id"])
                    anclas += 1
                    logger.debug("ancla_lexica_anexada", termino=termino, chunk=f["chunk_index"])

        return fragmentos, traduccion, anclas

    def _vecinos_en_el_grafo(self, documento_id: str, ref_id: str) -> list[dict]:
        """
        Papers de la propia bibliografía cercanos al citado, en orden de
        pertinencia: primero los que la fuente citada declara en su
        bibliografía, luego los que comparten autor, luego los que comparten
        concepto. Solo se devuelven los que están indexados.
        """
        vecinos: dict[str, dict] = {}
        consultas = (
            _Q_VECINOS_POR_CITA_EXTERNA,
            _Q_VECINOS_POR_AUTOR,
            _Q_VECINOS_POR_CONCEPTO,
        )
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                for query in consultas:
                    for rec in session.run(
                        query, doc_id=documento_id, ref_id=ref_id, limite=_MAX_VECINOS
                    ):
                        fila = dict(rec)
                        vecinos.setdefault(fila["ref_id"], fila)
                    if len(vecinos) >= _MAX_VECINOS:
                        break
        except Exception as e:
            logger.warning("error_vecinos_grafo", ref_id=ref_id, error=str(e))
            return []
        return list(vecinos.values())[:_MAX_VECINOS]

    def _buscar_en_vecinos(
        self, documento_id: str, ref_id: str, query: str,
        traduccion: str | None, n_fragmentos: int,
    ) -> tuple[dict, dict] | None:
        """
        Busca la afirmación en los papers vecinos. Devuelve (mejor_fragmento,
        vecino) o None. El umbral es MÁS alto que el de la búsqueda normal:
        señalar la fuente equivocada es peor que no señalar ninguna.
        """
        vecinos = self._vecinos_en_el_grafo(documento_id, ref_id)
        if not vecinos:
            return None

        mejor_global, vecino_global = None, None
        for vecino in vecinos:
            doi_norm = (vecino["doi"] or "").replace("/", "_").replace(".", "_")
            if not doi_norm or not supabase_vector_service.paper_ya_indexado(doi_norm):
                continue
            fragmentos, _, _ = self._buscar_fragmentos(
                query, doi_norm, n_fragmentos, traduccion=traduccion
            )
            if not fragmentos:
                continue
            mejor = fragmentos[0]
            if mejor["similitud"] < _UMBRAL_VECINO:
                continue
            if mejor_global is None or mejor["similitud"] > mejor_global["similitud"]:
                mejor_global, vecino_global = mejor, vecino

        if mejor_global is None:
            return None
        logger.info(
            "evidencia_hallada_en_vecino",
            ref_origen=ref_id, ref_vecina=vecino_global["ref_id"],
            via=vecino_global["via"], similitud=round(mejor_global["similitud"], 3),
        )
        return mejor_global, vecino_global

    @staticmethod
    def _guardar_consulta_en(cita_id: str, consulta_en: str) -> None:
        """
        Cachea la traducción al inglés en el nodo Cita. Sin esto se paga una
        llamada al LLM por cita en cada auditoría y la consulta cambia entre
        corridas, así que los resultados no son reproducibles.
        """
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                session.run(_Q_GUARDAR_CONSULTA_EN, cita_id=cita_id, consulta_en=consulta_en)
        except Exception as e:
            logger.warning("cache_traduccion_fallida", cita_id=cita_id, error=str(e))

    @staticmethod
    def _traducir_a_ingles(texto: str) -> str | None:
        """Traducción barata de la consulta (1-2 oraciones); None si falla."""
        try:
            traduccion = llm_service.completar(
                system_prompt=_SYSTEM_TRADUCCION,
                user_prompt=texto[:800],
            ).strip()
            return traduccion or None
        except Exception as e:
            logger.warning("traduccion_query_fallida", error=str(e))
            return None

    @staticmethod
    def _coser_ventana(doi_normalizado: str, fragmentos: list[dict]) -> str:
        """
        Ventana de contexto: el mejor chunk se expande con sus vecinos
        chunk_index ± 1 y se cose como pasaje continuo, para que la evidencia
        que cruza una frontera de chunk no produzca falsos NO_INFO. Los demás
        fragmentos (top-k y anclas léxicas) se anexan después, salvo que ya
        estén dentro de la ventana. Todo pasa por limpiar_fragmento: el juez,
        las cartillas y el Excel ven el mismo texto sin ruido de markdown.
        """
        mejor = fragmentos[0]
        idx = mejor.get("chunk_index")
        if idx is None:
            return "\n\n---\n\n".join(
                limpiar_fragmento(f["contenido"]) for f in fragmentos
            )[:3000]

        vecinos = supabase_vector_service.chunks_por_rango(
            doi_normalizado, idx - 1, idx + 1
        )
        if len(vecinos) > 1:
            pasaje = ""
            for v in vecinos:
                limpio = limpiar_fragmento(v["contenido"])
                pasaje = _unir_sin_solape(pasaje, limpio) if pasaje else limpio
            indices_ventana = {v["chunk_index"] for v in vecinos}
            logger.debug(
                "ventana_contexto_cosida",
                chunk_index=idx,
                vecinos=sorted(indices_ventana),
            )
        else:
            pasaje = limpiar_fragmento(mejor["contenido"])
            indices_ventana = {idx}

        extras = [
            limpiar_fragmento(f["contenido"])[:700]
            for f in fragmentos[1:]
            if f.get("chunk_index") not in indices_ventana
        ][:3]
        return "\n\n---\n\n".join([pasaje[:2400], *extras])

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
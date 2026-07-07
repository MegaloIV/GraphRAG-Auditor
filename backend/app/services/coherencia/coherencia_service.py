"""
Módulo de Coherencia Inter-Fuentes (diseño completo: docs/COHERENCIA.md).

Relaciona las evidencias recuperadas de los distintos papers para detectar si
las fuentes de la tesis se apoyan, se contradicen o ni siquiera se relacionan
entre sí. Multihop genuino sobre el grafo:

  - Construcción: el traversal Evidencia→Concepto←Evidencia (2 saltos) genera
    los candidatos; el LLM solo etiqueta las aristas de esos pares (evita el
    N² de comparar todo contra todo).
  - Consulta: los hallazgos son caminos de 4-6 saltos en Cypher puro
    (Cita→Evidencia→CONTRADICE→Evidencia→Referencia→Cita).

Las comparaciones son SIEMPRE entre fragmentos de los papers (Evidencia),
nunca entre aseveraciones del tesista: la comparación tesista-vs-paper ya
existe (el veredicto de la auditoría). El tesista re-entra por transitividad
en los hallazgos.

No hay retrieval nuevo: los insumos son los fragmento_evidencia que la
auditoría (EP-004) dejó persistidos en cada nodo Cita.
"""
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import structlog

from app.core.config import get_settings
from app.models.coherencia import (
    AristaMapa,
    CoherenciaResponse,
    ConceptoResumen,
    DetalleRelacion,
    HallazgoCoherencia,
    MapaCoherencia,
    NodoMapa,
    TipoHallazgo,
)
from app.services.externo.embedding_service import embedding_service
from app.services.grafo.neo4j_service import neo4j_service
from app.services.llm.openai_client import llm_service
from app.services.storage.supabase_storage_service import storage_service

logger = structlog.get_logger(__name__)
settings = get_settings()

# Versión del formato del cache coherencia/{doc}.json (recalcula caches viejos).
_VERSION_CACHE = 1

# Podas anti-clique (§5 del diseño)
_UMBRAL_FUSION = 0.90              # similitud coseno para fusionar conceptos sinónimos
_FRACCION_CONCEPTO_UBICUO = 0.60   # concepto en >60% de las referencias → no genera candidatos
_MAX_EVIDENCIAS_POR_CONCEPTO = 8   # sobre este número, solo top pares por similitud
_MAX_PARES_POR_CONCEPTO = 15
_MAX_PARES_TOTAL = 120
_LOTE_CONCEPTOS = 10               # textos por llamada al extractor de conceptos
_MAX_CHARS_EVIDENCIA = 1500
_MAX_CHARS_DETALLE = 500           # recorte de fragmentos en el drill-down de la UI

_TIPOS_RELACION = ("APOYA", "CONTRADICE", "COMPLEMENTA")


def _objeto_cache(documento_id: str) -> str:
    return f"coherencia/{documento_id}.json"


# ── Prompts (§7: reglas duras) ────────────────────────────────────────────────

SYSTEM_CONCEPTOS = """Eres un extractor de conceptos clave de textos académicos.
Para CADA texto numerado extrae de 2 a 4 conceptos.

Reglas:
- Sustantivos o frases nominales cortas (máximo 4 palabras), en español.
- Sin adjetivos valorativos ni verbos.
- Conceptos ESPECÍFICOS del contenido de cada texto; prohibido devolver el
  tema general del documento o términos genéricos como "investigación",
  "estudio" o "análisis".
- Responde ÚNICAMENTE con un JSON por línea, sin texto adicional:
{"texto_id": "<id>", "conceptos": ["concepto 1", "concepto 2"]}"""

SYSTEM_RELACION = """Eres un juez que compara dos fragmentos de papers académicos DISTINTOS.
Determina la relación entre lo que AFIRMAN los fragmentos.

Valores posibles:
- APOYA: ambos sostienen la misma conclusión sobre el mismo objeto.
- CONTRADICE: afirmaciones explícitamente incompatibles (no pueden ser ciertas a la vez).
- COMPLEMENTA: hablan de lo mismo aportando aspectos distintos o matices compatibles.
- NO_RELACIONADO: no hablan realmente de lo mismo.

Reglas estrictas:
- CONTRADICE solo ante incompatibilidad EXPLÍCITA. Diferencias de matiz,
  alcance, población o contexto → COMPLEMENTA.
- Ante la duda, responde NO_RELACIONADO. Un falso CONTRADICE es peor que uno
  omitido: es una acusación de inconsistencia al autor de la tesis.
- Evalúa el contenido semántico, no el idioma ni el estilo.

Responde ÚNICAMENTE en este formato exacto:
RELACION: <APOYA|CONTRADICE|COMPLEMENTA|NO_RELACIONADO>
CONFIANZA: <alta|media>
JUSTIFICACION: <una oración>"""


# ── Cypher ────────────────────────────────────────────────────────────────────

# Paso 1: insumos — citas auditadas con evidencia y su referencia.
_Q_EVIDENCIAS = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)-[:CITA_A]->(r:Referencia)
WHERE c.veredicto IN ['SUPPORTS', 'REFUTES']
  AND c.fragmento_evidencia IS NOT NULL AND c.fragmento_evidencia <> ''
RETURN c.id AS cita_id,
       c.fragmento AS aseveracion,
       c.veredicto AS veredicto,
       c.fragmento_evidencia AS evidencia,
       c.pagina_paper AS pagina_paper,
       r.id AS ref_id,
       coalesce(r.titulo_oficial, r.titulo) AS ref_titulo,
       r.anio AS ref_anio,
       coalesce(r.doi_verificado, r.doi) AS doi
"""

# Reconstrucción idempotente: borra el subgrafo previo del documento.
_Q_BORRAR_CONCEPTOS = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CONCEPTO]->(k:Concepto)
DETACH DELETE k
"""
_Q_BORRAR_EVIDENCIAS = """
MATCH (e:Evidencia {documento_id: $doc_id})
DETACH DELETE e
"""

_Q_CREAR_EVIDENCIA = """
MATCH (r:Referencia {id: $ref_id})
CREATE (e:Evidencia {id: $id, documento_id: $doc_id, texto: $texto,
                     pagina_paper: $pagina_paper, doi: $doi})
MERGE (e)-[:PROVIENE_DE]->(r)
WITH e
UNWIND $cita_ids AS cita_id
MATCH (c:Cita {id: cita_id})
MERGE (c)-[:EVIDENCIADA_POR]->(e)
"""

_Q_CREAR_CONCEPTO = """
MATCH (d:Documento {id: $doc_id})
CREATE (k:Concepto {id: $id, documento_id: $doc_id, nombre: $nombre,
                    variantes: $variantes, n_referencias: $n_referencias})
MERGE (d)-[:TIENE_CONCEPTO]->(k)
"""

_Q_MENCIONA = """
MATCH (e:Evidencia {id: $evidencia_id}), (k:Concepto {id: $concepto_id})
MERGE (e)-[:MENCIONA]->(k)
"""

_Q_SOBRE = """
MATCH (c:Cita {id: $cita_id}), (k:Concepto {id: $concepto_id})
MERGE (c)-[:SOBRE]->(k)
"""

# Paso 4: candidatos por traversal — pares de evidencias que comparten
# concepto y provienen de referencias distintas (§4 del diseño).
_Q_CANDIDATOS = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CONCEPTO]->(k:Concepto),
      (e1:Evidencia)-[:MENCIONA]->(k)<-[:MENCIONA]-(e2:Evidencia),
      (e1)-[:PROVIENE_DE]->(r1:Referencia),
      (e2)-[:PROVIENE_DE]->(r2:Referencia)
WHERE e1.id < e2.id AND r1.id <> r2.id
RETURN e1.id AS e1, e2.id AS e2, collect(DISTINCT k.id) AS conceptos
"""

# Paso 6: hallazgos (multihop de consulta, §6 del diseño).
_Q_CONTRADICCIONES = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c1:Cita)
      -[:EVIDENCIADA_POR]->(e1:Evidencia)
      -[rel:CONTRADICE]-(e2:Evidencia)
      <-[:EVIDENCIADA_POR]-(c2:Cita)<-[:TIENE_CITA]-(d)
MATCH (e1)-[:PROVIENE_DE]->(r1:Referencia), (e2)-[:PROVIENE_DE]->(r2:Referencia)
WHERE c1.id < c2.id
RETURN DISTINCT c1.id AS cita_1, c2.id AS cita_2,
       r1.id AS ref_1, r2.id AS ref_2,
       coalesce(r1.titulo_oficial, r1.titulo) AS titulo_1,
       coalesce(r2.titulo_oficial, r2.titulo) AS titulo_2,
       rel.justificacion AS justificacion, rel.confianza AS confianza
"""

_Q_TRIANGULACIONES = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
      -[:EVIDENCIADA_POR]->(e1:Evidencia)-[rel:APOYA]-(e2:Evidencia),
      (e1)-[:PROVIENE_DE]->(r1:Referencia), (e2)-[:PROVIENE_DE]->(r2:Referencia)
WHERE r1.id <> r2.id AND c.veredicto = 'SUPPORTS'
RETURN c.id AS cita_id, r1.id AS ref_id,
       coalesce(r1.titulo_oficial, r1.titulo) AS fuente,
       collect(DISTINCT coalesce(r2.titulo_oficial, r2.titulo)) AS convergen,
       collect(DISTINCT r2.id) AS convergen_ids
"""

_Q_FUENTES_ISLA = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
      <-[:PROVIENE_DE]-(e:Evidencia)
OPTIONAL MATCH (e)-[:MENCIONA]->(:Concepto)<-[:MENCIONA]-(e2:Evidencia)
      -[:PROVIENE_DE]->(r2:Referencia)
WHERE r2.id <> r.id
WITH r, collect(DISTINCT e.id) AS evidencias, count(r2) AS vecinos
WHERE vecinos = 0
RETURN r.id AS ref_id,
       coalesce(r.titulo_oficial, r.titulo) AS titulo,
       evidencias
"""

_Q_CONCEPTOS_DEBILES = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CONCEPTO]->(k:Concepto)
WHERE k.n_referencias = 1
MATCH (c:Cita)-[:SOBRE]->(k)
WITH k, collect(DISTINCT c.id) AS citas
WHERE size(citas) >= 2
OPTIONAL MATCH (e:Evidencia)-[:MENCIONA]->(k), (e)-[:PROVIENE_DE]->(r:Referencia)
RETURN k.nombre AS concepto, citas,
       collect(DISTINCT r.id) AS ref_ids,
       collect(DISTINCT coalesce(r.titulo_oficial, r.titulo)) AS titulos
"""


class _UnionFind:
    """Fusión de conceptos sinónimos (paso 3)."""

    def __init__(self, n: int):
        self.padre = list(range(n))

    def raiz(self, i: int) -> int:
        while self.padre[i] != i:
            self.padre[i] = self.padre[self.padre[i]]
            i = self.padre[i]
        return i

    def unir(self, a: int, b: int) -> None:
        ra, rb = self.raiz(a), self.raiz(b)
        if ra != rb:
            self.padre[rb] = ra


class CoherenciaService:

    # ── API pública ──────────────────────────────────────────────────────────

    def construir(self, documento_id: str) -> CoherenciaResponse:
        """
        Ejecuta el flujo completo (7 pasos, §4 del diseño) y persiste el
        resultado en Neo4j + Storage. Idempotente: reconstruye el subgrafo
        del documento desde cero en cada corrida.
        """
        # 1. Recolección
        filas = self._leer_insumos(documento_id)
        if not filas:
            logger.info("coherencia_sin_insumos", doc_id=documento_id)
            return CoherenciaResponse(documento_id=documento_id)

        evidencias, aseveraciones = self._deduplicar_evidencias(filas)
        logger.info(
            "coherencia_insumos",
            doc_id=documento_id,
            citas_auditadas=len(filas),
            evidencias_unicas=len(evidencias),
        )

        # 2. Conceptos (LLM por lotes)
        conceptos_por_texto = self._extraer_conceptos(evidencias, aseveraciones)

        # 3. Fusión de sinónimos + persistencia del subgrafo base
        conceptos = self._fusionar_conceptos(conceptos_por_texto, evidencias, aseveraciones)
        self._persistir_subgrafo(documento_id, evidencias, conceptos)

        # 4. Candidatos por traversal + podas anti-clique
        candidatos = self._generar_candidatos(documento_id, evidencias, conceptos)

        # 5. Juez de aristas (LLM en paralelo)
        relaciones = self._juzgar_pares(candidatos, evidencias, conceptos)
        self._persistir_relaciones(relaciones)

        # 6. Hallazgos (Cypher multihop puro)
        hallazgos = self._derivar_hallazgos(documento_id)

        # 7. Mapa para la UI + cache en Storage
        mapa = self._construir_mapa(evidencias, conceptos, relaciones, candidatos, hallazgos)
        respuesta = CoherenciaResponse(
            documento_id=documento_id,
            construido_en=datetime.now(timezone.utc).isoformat(),
            total_evidencias=len(evidencias),
            total_conceptos=len(conceptos),
            total_comparaciones=len(candidatos),
            total_relaciones=len(relaciones),
            hallazgos=hallazgos,
            mapa=mapa,
        )
        storage_service.subir_texto(
            _objeto_cache(documento_id),
            json.dumps(
                {"version": _VERSION_CACHE, "respuesta": respuesta.model_dump()},
                ensure_ascii=False,
            ),
        )
        logger.info(
            "coherencia_construida",
            doc_id=documento_id,
            evidencias=len(evidencias),
            conceptos=len(conceptos),
            comparaciones=len(candidatos),
            relaciones=len(relaciones),
            hallazgos=len(hallazgos),
        )
        return respuesta

    def obtener(self, documento_id: str) -> CoherenciaResponse | None:
        """Resultado cacheado en Storage, o None si aún no se construyó."""
        crudo = storage_service.leer_texto(_objeto_cache(documento_id))
        if crudo is None:
            return None
        try:
            datos = json.loads(crudo)
            if datos.get("version") != _VERSION_CACHE:
                return None
            return CoherenciaResponse(**datos["respuesta"])
        except Exception as e:
            logger.warning("cache_coherencia_corrupto", doc_id=documento_id, error=str(e))
            return None

    def invalidar_cache(self, documento_id: str) -> None:
        """Se llama al re-auditar: los veredictos nuevos invalidan el análisis."""
        storage_service.eliminar(_objeto_cache(documento_id))

    # ── Paso 1: recolección ──────────────────────────────────────────────────

    def _leer_insumos(self, documento_id: str) -> list[dict]:
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            return [dict(rec) for rec in session.run(_Q_EVIDENCIAS, doc_id=documento_id)]

    @staticmethod
    def _deduplicar_evidencias(filas: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Evidencias únicas por (referencia, texto normalizado): dos citas
        auditadas contra el mismo fragmento comparten el nodo Evidencia.
        Retorna (evidencias, aseveraciones).
        """
        def _norm(s: str) -> str:
            return re.sub(r"\s+", " ", s or "").strip().lower()

        evidencias: dict[tuple, dict] = {}
        aseveraciones: list[dict] = []

        for fila in filas:
            texto = (fila["evidencia"] or "")[:_MAX_CHARS_EVIDENCIA]
            clave = (fila["ref_id"], _norm(texto))
            if clave not in evidencias:
                evidencias[clave] = {
                    "id": str(uuid.uuid4()),
                    "texto": texto,
                    "pagina_paper": fila["pagina_paper"],
                    "doi": fila["doi"],
                    "ref_id": fila["ref_id"],
                    "ref_titulo": fila["ref_titulo"] or "Sin título",
                    "ref_anio": fila["ref_anio"],
                    "cita_ids": [],
                    "veredictos": set(),
                }
            evidencias[clave]["cita_ids"].append(fila["cita_id"])
            evidencias[clave]["veredictos"].add(fila["veredicto"])

            if (fila["aseveracion"] or "").strip():
                aseveraciones.append({
                    "cita_id": fila["cita_id"],
                    "texto": fila["aseveracion"].strip()[:_MAX_CHARS_EVIDENCIA],
                })

        return list(evidencias.values()), aseveraciones

    # ── Paso 2: extracción de conceptos ──────────────────────────────────────

    def _extraer_conceptos(
        self, evidencias: list[dict], aseveraciones: list[dict]
    ) -> dict[str, list[str]]:
        """
        Conceptos por texto, en lotes. Claves: id de la Evidencia, o
        "cita::{cita_id}" para las aseveraciones del tesista.
        """
        textos = [(e["id"], e["texto"]) for e in evidencias]
        textos += [(f"cita::{a['cita_id']}", a["texto"]) for a in aseveraciones]

        resultado: dict[str, list[str]] = {}
        for inicio in range(0, len(textos), _LOTE_CONCEPTOS):
            lote = textos[inicio:inicio + _LOTE_CONCEPTOS]
            cuerpo = "\n\n".join(
                f"TEXTO {tid}:\n{contenido[:800]}" for tid, contenido in lote
            )
            try:
                respuesta = llm_service.completar(
                    system_prompt=SYSTEM_CONCEPTOS,
                    user_prompt=f"Extrae los conceptos de cada texto:\n\n{cuerpo}",
                )
                for obj in self._parsear_json_lineas(respuesta):
                    tid = str(obj.get("texto_id", "")).strip()
                    conceptos = [
                        str(c).strip() for c in obj.get("conceptos", [])
                        if str(c).strip()
                    ]
                    if tid and conceptos:
                        resultado[tid] = conceptos[:4]
            except Exception as e:
                logger.error("error_extraccion_conceptos", lote=inicio, error=str(e))

        logger.info("conceptos_extraidos", textos=len(textos), con_conceptos=len(resultado))
        return resultado

    # ── Paso 3: fusión de sinónimos ──────────────────────────────────────────

    def _fusionar_conceptos(
        self,
        conceptos_por_texto: dict[str, list[str]],
        evidencias: list[dict],
        aseveraciones: list[dict],
    ) -> list[dict]:
        """
        Normaliza nombres, fusiona sinónimos por embedding (union-find,
        similitud ≥ _UMBRAL_FUSION) y arma la estructura final de conceptos
        con sus evidencias, citas y referencias.
        """
        def _norm(s: str) -> str:
            return re.sub(r"\s+", " ", s).strip().lower()

        # Nombres únicos normalizados, con frecuencia para elegir el canónico.
        frecuencia: dict[str, int] = {}
        for nombres in conceptos_por_texto.values():
            for n in nombres:
                frecuencia[_norm(n)] = frecuencia.get(_norm(n), 0) + 1
        nombres_unicos = list(frecuencia.keys())
        if not nombres_unicos:
            return []

        # Fusión por similitud de embeddings.
        uf = _UnionFind(len(nombres_unicos))
        if len(nombres_unicos) > 1:
            try:
                vectores = np.array(
                    embedding_service.generar_embeddings(nombres_unicos),
                    dtype=np.float32,
                )
                normas = np.linalg.norm(vectores, axis=1, keepdims=True)
                normalizados = vectores / np.clip(normas, 1e-9, None)
                similitudes = normalizados @ normalizados.T
                for i in range(len(nombres_unicos)):
                    for j in range(i + 1, len(nombres_unicos)):
                        if similitudes[i, j] >= _UMBRAL_FUSION:
                            uf.unir(i, j)
            except Exception as e:
                # Sin embeddings, cada nombre normalizado es su propio concepto.
                logger.warning("fusion_conceptos_sin_embeddings", error=str(e))

        indice_por_nombre = {n: i for i, n in enumerate(nombres_unicos)}
        grupos: dict[int, list[str]] = {}
        for nombre, i in indice_por_nombre.items():
            grupos.setdefault(uf.raiz(i), []).append(nombre)

        conceptos: list[dict] = []
        concepto_por_nombre: dict[str, dict] = {}
        for miembros in grupos.values():
            canonico = max(miembros, key=lambda n: frecuencia[n])
            concepto = {
                "id": str(uuid.uuid4()),
                "nombre": canonico,
                "variantes": sorted(m for m in miembros if m != canonico),
                "evidencia_ids": set(),
                "cita_ids": set(),
                "ref_ids": set(),
            }
            conceptos.append(concepto)
            for m in miembros:
                concepto_por_nombre[m] = concepto

        # Asignaciones texto → concepto.
        ref_por_evidencia = {e["id"]: e["ref_id"] for e in evidencias}
        for tid, nombres in conceptos_por_texto.items():
            for nombre in nombres:
                concepto = concepto_por_nombre.get(_norm(nombre))
                if concepto is None:
                    continue
                if tid.startswith("cita::"):
                    concepto["cita_ids"].add(tid.removeprefix("cita::"))
                elif tid in ref_por_evidencia:
                    concepto["evidencia_ids"].add(tid)
                    concepto["ref_ids"].add(ref_por_evidencia[tid])

        # Sin evidencia ni cita que lo mencione, el concepto no aporta.
        conceptos = [c for c in conceptos if c["evidencia_ids"] or c["cita_ids"]]
        logger.info("conceptos_fusionados", unicos=len(nombres_unicos), finales=len(conceptos))
        return conceptos

    # ── Persistencia del subgrafo (pasos 3 y 5) ──────────────────────────────

    def _persistir_subgrafo(
        self, documento_id: str, evidencias: list[dict], conceptos: list[dict]
    ) -> None:
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            # Reconstrucción idempotente
            session.run(_Q_BORRAR_CONCEPTOS, doc_id=documento_id)
            session.run(_Q_BORRAR_EVIDENCIAS, doc_id=documento_id)

            for e in evidencias:
                session.run(
                    _Q_CREAR_EVIDENCIA,
                    id=e["id"],
                    doc_id=documento_id,
                    texto=e["texto"],
                    pagina_paper=e["pagina_paper"],
                    doi=e["doi"],
                    ref_id=e["ref_id"],
                    cita_ids=e["cita_ids"],
                )

            for k in conceptos:
                session.run(
                    _Q_CREAR_CONCEPTO,
                    id=k["id"],
                    doc_id=documento_id,
                    nombre=k["nombre"],
                    variantes=k["variantes"],
                    n_referencias=len(k["ref_ids"]),
                )
                for eid in k["evidencia_ids"]:
                    session.run(_Q_MENCIONA, evidencia_id=eid, concepto_id=k["id"])
                for cid in k["cita_ids"]:
                    session.run(_Q_SOBRE, cita_id=cid, concepto_id=k["id"])

    def _persistir_relaciones(self, relaciones: list[dict]) -> None:
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            for rel in relaciones:
                tipo = rel["tipo"]
                if tipo not in _TIPOS_RELACION:
                    continue
                # El tipo de relación no es parametrizable en Cypher; viene de
                # la whitelist _TIPOS_RELACION, nunca del LLM directamente.
                session.run(
                    f"""
                    MATCH (e1:Evidencia {{id: $id1}}), (e2:Evidencia {{id: $id2}})
                    CREATE (e1)-[:{tipo} {{confianza: $confianza,
                                           justificacion: $justificacion,
                                           concepto_id: $concepto_id,
                                           evaluado_en: datetime()}}]->(e2)
                    """,
                    id1=rel["e1"],
                    id2=rel["e2"],
                    confianza=rel["confianza"],
                    justificacion=rel["justificacion"],
                    concepto_id=rel["concepto_id"],
                )

    # ── Paso 4: candidatos + podas ───────────────────────────────────────────

    def _generar_candidatos(
        self, documento_id: str, evidencias: list[dict], conceptos: list[dict]
    ) -> list[dict]:
        """
        Traversal Evidencia→Concepto←Evidencia entre referencias distintas,
        seguido de las podas anti-clique (§5): conceptos ubicuos fuera, cap
        por concepto y presupuesto global priorizando citas REFUTES.
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            pares_crudos = [dict(rec) for rec in session.run(_Q_CANDIDATOS, doc_id=documento_id)]

        if not pares_crudos:
            return []

        total_refs = len({e["ref_id"] for e in evidencias})
        ubicuos = {
            k["id"] for k in conceptos
            if total_refs >= 3 and len(k["ref_ids"]) / total_refs > _FRACCION_CONCEPTO_UBICUO
        }
        if ubicuos:
            logger.info("conceptos_ubicuos_descartados", total=len(ubicuos))

        evidencia_por_id = {e["id"]: e for e in evidencias}

        # Poda 1: pares cuyo único vínculo son conceptos ubicuos, fuera.
        pares = []
        for p in pares_crudos:
            utiles = [k for k in p["conceptos"] if k not in ubicuos]
            if utiles:
                pares.append({"e1": p["e1"], "e2": p["e2"], "conceptos": utiles})

        # Similitud entre evidencias, para las podas 2 y 3.
        try:
            ids = [e["id"] for e in evidencias]
            vectores = np.array(
                embedding_service.generar_embeddings([e["texto"] for e in evidencias]),
                dtype=np.float32,
            )
            normas = np.linalg.norm(vectores, axis=1, keepdims=True)
            normalizados = vectores / np.clip(normas, 1e-9, None)
            indice = {eid: i for i, eid in enumerate(ids)}

            def _sim(a: str, b: str) -> float:
                return float(normalizados[indice[a]] @ normalizados[indice[b]])
        except Exception as e:
            logger.warning("candidatos_sin_similitud_embeddings", error=str(e))

            def _sim(a: str, b: str) -> float:
                return 0.0

        # Poda 2: cap por concepto — conceptos con demasiadas evidencias solo
        # aportan sus pares más similares.
        evidencias_por_concepto: dict[str, set[str]] = {}
        for p in pares:
            for k in p["conceptos"]:
                evidencias_por_concepto.setdefault(k, set()).update((p["e1"], p["e2"]))

        pares_por_concepto: dict[str, list[dict]] = {}
        for p in pares:
            for k in p["conceptos"]:
                pares_por_concepto.setdefault(k, []).append(p)

        permitidos: set[tuple[str, str]] = set()
        for k, lista in pares_por_concepto.items():
            if len(evidencias_por_concepto.get(k, set())) > _MAX_EVIDENCIAS_POR_CONCEPTO:
                lista = sorted(lista, key=lambda p: _sim(p["e1"], p["e2"]), reverse=True)
                lista = lista[:_MAX_PARES_POR_CONCEPTO]
            permitidos.update((p["e1"], p["e2"]) for p in lista)

        pares = [p for p in pares if (p["e1"], p["e2"]) in permitidos]

        # Poda 3: presupuesto global — prioriza pares con citas REFUTES
        # (mayor probabilidad de conflicto informativo) y mayor similitud.
        def _prioridad(p: dict) -> tuple:
            con_refutes = any(
                "REFUTES" in evidencia_por_id[eid]["veredictos"]
                for eid in (p["e1"], p["e2"])
            )
            return (con_refutes, _sim(p["e1"], p["e2"]))

        pares.sort(key=_prioridad, reverse=True)
        if len(pares) > _MAX_PARES_TOTAL:
            logger.info("presupuesto_global_aplicado", antes=len(pares), despues=_MAX_PARES_TOTAL)
            pares = pares[:_MAX_PARES_TOTAL]

        logger.info("candidatos_generados", crudos=len(pares_crudos), finales=len(pares))
        return pares

    # ── Paso 5: juez de aristas ──────────────────────────────────────────────

    def _juzgar_pares(
        self, candidatos: list[dict], evidencias: list[dict], conceptos: list[dict]
    ) -> list[dict]:
        if not candidatos:
            return []

        evidencia_por_id = {e["id"]: e for e in evidencias}
        concepto_por_id = {k["id"]: k for k in conceptos}

        def _juzgar(par: dict) -> dict | None:
            e1 = evidencia_por_id[par["e1"]]
            e2 = evidencia_por_id[par["e2"]]
            concepto_id = par["conceptos"][0]
            nombre_concepto = concepto_por_id.get(concepto_id, {}).get("nombre", "")
            try:
                respuesta = llm_service.completar(
                    system_prompt=SYSTEM_RELACION,
                    user_prompt=(
                        f"Concepto compartido: {nombre_concepto}\n\n"
                        f"FRAGMENTO A — {e1['ref_titulo']} ({e1['ref_anio'] or 's.f.'}):\n"
                        f"{e1['texto']}\n\n"
                        f"FRAGMENTO B — {e2['ref_titulo']} ({e2['ref_anio'] or 's.f.'}):\n"
                        f"{e2['texto']}"
                    ),
                )
                tipo, confianza, justificacion = self._parsear_relacion(respuesta)
                if tipo not in _TIPOS_RELACION:
                    return None
                return {
                    "e1": par["e1"],
                    "e2": par["e2"],
                    "tipo": tipo,
                    "confianza": confianza,
                    "justificacion": justificacion,
                    "concepto_id": concepto_id,
                }
            except Exception as e:
                logger.error("error_juez_relacion", e1=par["e1"], e2=par["e2"], error=str(e))
                return None

        relaciones: list[dict] = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            for resultado in executor.map(_juzgar, candidatos):
                if resultado is not None:
                    relaciones.append(resultado)

        logger.info(
            "pares_juzgados",
            comparados=len(candidatos),
            con_relacion=len(relaciones),
            contradicciones=sum(1 for r in relaciones if r["tipo"] == "CONTRADICE"),
        )
        return relaciones

    # ── Paso 6: hallazgos ────────────────────────────────────────────────────

    def _derivar_hallazgos(self, documento_id: str) -> list[HallazgoCoherencia]:
        hallazgos: list[HallazgoCoherencia] = []
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            for rec in session.run(_Q_CONTRADICCIONES, doc_id=documento_id):
                hallazgos.append(HallazgoCoherencia(
                    tipo=TipoHallazgo.CONTRADICCION_INTERNA,
                    cita_ids=[rec["cita_1"], rec["cita_2"]],
                    referencia_ids=[rec["ref_1"], rec["ref_2"]],
                    referencias=[rec["titulo_1"], rec["titulo_2"]],
                    justificacion=rec["justificacion"] or "Las fuentes se contradicen.",
                    confianza=rec["confianza"],
                ))

            for rec in session.run(_Q_TRIANGULACIONES, doc_id=documento_id):
                hallazgos.append(HallazgoCoherencia(
                    tipo=TipoHallazgo.TRIANGULACION,
                    cita_ids=[rec["cita_id"]],
                    referencia_ids=[rec["ref_id"], *rec["convergen_ids"]],
                    referencias=[rec["fuente"], *rec["convergen"]],
                    justificacion=(
                        f"La evidencia de “{rec['fuente']}” converge con "
                        f"{len(rec['convergen'])} fuente(s) independiente(s) más."
                    ),
                ))

            for rec in session.run(_Q_FUENTES_ISLA, doc_id=documento_id):
                hallazgos.append(HallazgoCoherencia(
                    tipo=TipoHallazgo.FUENTE_ISLA,
                    referencia_ids=[rec["ref_id"]],
                    referencias=[rec["titulo"]],
                    justificacion=(
                        "Sus evidencias no comparten ningún concepto con las demás "
                        "fuentes citadas: posible cita decorativa o tema aislado."
                    ),
                ))

            for rec in session.run(_Q_CONCEPTOS_DEBILES, doc_id=documento_id):
                hallazgos.append(HallazgoCoherencia(
                    tipo=TipoHallazgo.CONCEPTO_DEBIL,
                    cita_ids=rec["citas"],
                    referencia_ids=rec["ref_ids"],
                    referencias=rec["titulos"],
                    concepto=rec["concepto"],
                    justificacion=(
                        f"El concepto “{rec['concepto']}” aparece en "
                        f"{len(rec['citas'])} citas pero lo sostiene una sola referencia."
                    ),
                ))

        # Las contradicciones (gravedad alta) primero.
        orden = {
            TipoHallazgo.CONTRADICCION_INTERNA: 0,
            TipoHallazgo.FUENTE_ISLA: 1,
            TipoHallazgo.CONCEPTO_DEBIL: 2,
            TipoHallazgo.TRIANGULACION: 3,
        }
        hallazgos.sort(key=lambda h: orden[h.tipo])
        return hallazgos

    # ── Paso 7: mapa de referencias (proyección para la UI, §9) ──────────────

    def _construir_mapa(
        self,
        evidencias: list[dict],
        conceptos: list[dict],
        relaciones: list[dict],
        candidatos: list[dict],
        hallazgos: list[HallazgoCoherencia],
    ) -> MapaCoherencia:
        evidencia_por_id = {e["id"]: e for e in evidencias}
        concepto_por_id = {k["id"]: k for k in conceptos}
        islas = {
            rid
            for h in hallazgos if h.tipo == TipoHallazgo.FUENTE_ISLA
            for rid in h.referencia_ids
        }

        # Nodos: una entrada por referencia con evidencias.
        nodos: dict[str, NodoMapa] = {}
        for e in evidencias:
            rid = e["ref_id"]
            if rid not in nodos:
                titulo = e["ref_titulo"]
                anio = e["ref_anio"]
                etiqueta = titulo if len(titulo) <= 48 else titulo[:45] + "…"
                if anio:
                    etiqueta = f"{etiqueta} ({anio})"
                nodos[rid] = NodoMapa(
                    id=rid,
                    label=etiqueta,
                    titulo=titulo,
                    anio=anio,
                    es_isla=rid in islas,
                )
            nodos[rid].n_citas = len(
                {c for ev in evidencias if ev["ref_id"] == rid for c in ev["cita_ids"]}
            )

        # Aristas semánticas agregadas por (par de referencias, tipo).
        agregadas: dict[tuple[str, str, str], AristaMapa] = {}
        for rel in relaciones:
            e1, e2 = evidencia_por_id[rel["e1"]], evidencia_por_id[rel["e2"]]
            r1, r2 = sorted((e1["ref_id"], e2["ref_id"]))
            clave = (r1, r2, rel["tipo"])
            if clave not in agregadas:
                agregadas[clave] = AristaMapa(source=r1, target=r2, tipo=rel["tipo"], pares=0)
            arista = agregadas[clave]
            arista.pares += 1
            arista.detalles.append(DetalleRelacion(
                evidencia_1=e1["texto"][:_MAX_CHARS_DETALLE],
                evidencia_2=e2["texto"][:_MAX_CHARS_DETALLE],
                justificacion=rel["justificacion"],
                confianza=rel["confianza"],
                concepto=concepto_por_id.get(rel["concepto_id"], {}).get("nombre"),
                cita_ids=sorted(set(e1["cita_ids"]) | set(e2["cita_ids"])),
            ))

        # Co-mención: pares de referencias que comparten concepto pero sin
        # ninguna relación semántica juzgada entre ellas.
        con_relacion = {(a.source, a.target) for a in agregadas.values()}
        co_mencion: dict[tuple[str, str], int] = {}
        for k in conceptos:
            refs = sorted(k["ref_ids"])
            for i in range(len(refs)):
                for j in range(i + 1, len(refs)):
                    par = (refs[i], refs[j])
                    if par not in con_relacion and par[0] in nodos and par[1] in nodos:
                        co_mencion[par] = co_mencion.get(par, 0) + 1
        for (r1, r2), cuenta in co_mencion.items():
            agregadas[(r1, r2, "CO_MENCION")] = AristaMapa(
                source=r1, target=r2, tipo="CO_MENCION", pares=cuenta,
            )

        conceptos_resumen = sorted(
            (
                ConceptoResumen(
                    id=k["id"],
                    nombre=k["nombre"],
                    n_referencias=len(k["ref_ids"]),
                    referencia_ids=sorted(k["ref_ids"]),
                )
                for k in conceptos if k["ref_ids"]
            ),
            key=lambda c: c.n_referencias,
            reverse=True,
        )

        return MapaCoherencia(
            nodes=list(nodos.values()),
            links=list(agregadas.values()),
            conceptos=conceptos_resumen,
        )

    # ── Parsers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parsear_json_lineas(texto: str) -> list[dict]:
        limpio = re.sub(r"```json\s*|\s*```", "", texto).strip()
        objetos: list[dict] = []
        for linea in limpio.splitlines():
            linea = linea.strip()
            if not linea.startswith("{"):
                continue
            try:
                obj = json.loads(linea)
                if isinstance(obj, dict):
                    objetos.append(obj)
            except json.JSONDecodeError:
                continue
        if not objetos:
            try:
                datos = json.loads(limpio)
                if isinstance(datos, list):
                    objetos = [o for o in datos if isinstance(o, dict)]
            except json.JSONDecodeError:
                pass
        return objetos

    @staticmethod
    def _parsear_relacion(texto: str) -> tuple[str, str, str]:
        tipo_m = re.search(
            r"RELACION:\s*(APOYA|CONTRADICE|COMPLEMENTA|NO_RELACIONADO)", texto, re.IGNORECASE
        )
        conf_m = re.search(r"CONFIANZA:\s*(alta|media)", texto, re.IGNORECASE)
        just_m = re.search(r"JUSTIFICACION:\s*(.+)", texto, re.IGNORECASE)
        tipo = tipo_m.group(1).upper() if tipo_m else "NO_RELACIONADO"
        confianza = conf_m.group(1).lower() if conf_m else "media"
        justificacion = just_m.group(1).strip() if just_m else ""
        return tipo, confianza, justificacion


# Singleton
coherencia_service = CoherenciaService()

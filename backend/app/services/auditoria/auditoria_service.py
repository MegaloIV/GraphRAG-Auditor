"""
EP-004: Auditoría Semántica y Detección de Alucinaciones

Flujo por cita:
  1. recuperacion_service  → fragmento del paper + nodos del grafo
  2. llm_service           → GPT-4o-mini decide el veredicto
  3. Neo4j                 → persiste el veredicto en el nodo Cita

Veredictos:
  VÁLIDA         → la cita es fiel al fragmento encontrado
  DUDOSA         → hay referencia pero la cita distorsiona o exagera
  ALUCINADA      → no hay evidencia que respalde la afirmación
  NO_VERIFICABLE → el sistema no encontró fragmento ni nodo para verificar
"""
import structlog
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service
from app.services.recuperacion.recuperacion_service import recuperacion_service
from app.services.vectorstore.supabase_service import supabase_vector_service
from app.services.llm.openai_client import llm_service
from app.models.auditoria import (
    VeredictoAuditoria,
    VeredictoTipo,
    AlertaInconsistencia,
    AlertaAlucinacionSistema,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

SYSTEM_AUDITORIA = """Eres un auditor académico experto en normas APA 7ma edición.
Tu tarea es verificar si una cita bibliográfica es fiel a su fuente original.

Se te dará:
- AFIRMACIÓN DEL TESISTA: la oración completa donde aparece la cita en la tesis
- CITA APA: la referencia entre paréntesis o narrativa
- FRAGMENTO DEL PAPER: el texto real del paper citado recuperado semánticamente

Responde ÚNICAMENTE con uno de estos veredictos y su justificación en este formato exacto:
VEREDICTO: <VÁLIDA|DUDOSA|ALUCINADA>
JUSTIFICACIÓN: <una sola oración explicando el veredicto>

Criterios:
- VÁLIDA: la afirmación del tesista refleja fielmente lo que dice el fragmento del paper
- DUDOSA: la afirmación exagera, simplifica en exceso o tergiversa parcialmente el fragmento
- ALUCINADA: la afirmación dice algo que el fragmento no dice o contradice directamente"""


_Q_CITAS_DOCUMENTO = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
OPTIONAL MATCH (c)-[:CITA_A]->(r:Referencia)
OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
RETURN
  c.id        AS cita_id,
  c.texto     AS texto_cita,
  c.fragmento AS fragmento_oracion,
  c.pagina    AS pagina,
  r.id        AS ref_id,
  r.titulo    AS ref_titulo,
  r.titulo_oficial AS ref_titulo_oficial,
  r.anio      AS ref_anio,
  r.doi_verificado AS doi_verificado,
  collect(DISTINCT a.nombre) AS autores
"""

_Q_REFERENCIAS_DOCUMENTO = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
RETURN r.id AS ref_id, r.titulo AS titulo, r.anio AS anio
"""

_Q_REFERENCIAS_CITADAS = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)-[:CITA_A]->(r:Referencia)
RETURN DISTINCT r.id AS ref_id
"""

_Q_GUARDAR_VEREDICTO = """
MATCH (c:Cita {id: $cita_id})
SET c.veredicto           = $veredicto,
    c.justificacion       = $justificacion,
    c.auditado_en         = datetime(),
    c.pagina_paper        = $pagina_paper,
    c.fragmento_evidencia = $fragmento_evidencia
"""


class AuditoriaService:

    # ── HU-010: Auditar todas las citas del documento ────────────────────

    def auditar_documento(self, documento_id: str) -> list[VeredictoAuditoria]:
        """
        Itera todas las citas del documento, recupera evidencia
        y emite un veredicto con GPT-4o-mini por cada una.
        Persiste el veredicto en Neo4j.
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            citas_raw = list(session.run(_Q_CITAS_DOCUMENTO, doc_id=documento_id))

        if not citas_raw:
            logger.info("auditoria_sin_citas", doc_id=documento_id)
            return []

        veredictos = []
        total = len(citas_raw)

        logger.info("auditoria_paralela_iniciada", doc_id=documento_id, total=total, max_workers=5)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    self._auditar_cita,
                    documento_id=documento_id,
                    cita_id=registro["cita_id"],
                    texto_cita=registro["texto_cita"] or "",
                    fragmento_oracion=registro["fragmento_oracion"] or "",
                    pagina=registro["pagina"] or 0,
                    ref_id=registro["ref_id"],
                    ref_titulo=registro["ref_titulo_oficial"] or registro["ref_titulo"] or "",
                    ref_anio=registro["ref_anio"],
                    autores=registro["autores"] or [],
                    doi_verificado=registro["doi_verificado"],
                ): registro
                for registro in citas_raw
            }
            for future in as_completed(futures):
                try:
                    veredicto = future.result()
                    veredictos.append(veredicto)
                except Exception as e:
                    logger.error("error_auditando_cita", error=str(e))

        logger.info(
            "auditoria_completada",
            doc_id=documento_id,
            total=total,
            validas=sum(1 for v in veredictos if v.veredicto == VeredictoTipo.VALIDA),
            dudosas=sum(1 for v in veredictos if v.veredicto == VeredictoTipo.DUDOSA),
            alucinadas=sum(1 for v in veredictos if v.veredicto == VeredictoTipo.ALUCINADA),
            no_verificables=sum(1 for v in veredictos if v.veredicto == VeredictoTipo.NO_VERIFICABLE),
        )
        return veredictos

    # ── HU-011: Alertas de inconsistencias estructurales ────────────────

    def detectar_inconsistencias(self, documento_id: str) -> dict:
        """
        Detecta:
        - Citas sin referencia bibliográfica (no tienen CITA_A)
        - Referencias listadas que nunca se citan en el cuerpo
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            citas_raw = list(session.run(_Q_CITAS_DOCUMENTO, doc_id=documento_id))
            refs_raw  = list(session.run(_Q_REFERENCIAS_DOCUMENTO, doc_id=documento_id))
            refs_citadas_raw = list(session.run(_Q_REFERENCIAS_CITADAS, doc_id=documento_id))

        ids_refs_citadas = {r["ref_id"] for r in refs_citadas_raw}

        # Citas sin referencia vinculada
        citas_sin_ref = [
            AlertaInconsistencia(
                tipo="cita_sin_referencia",
                descripcion="La cita no tiene una referencia bibliográfica correspondiente en la lista de referencias.",
                elemento=r["texto_cita"] or "",
                ubicacion=f"Página {r['pagina'] or '?'}",
            )
            for r in citas_raw
            if not r["ref_id"]
        ]

        # Referencias sin citar en el cuerpo
        refs_sin_citar = [
            AlertaInconsistencia(
                tipo="referencia_sin_citar",
                descripcion="La referencia está listada en la bibliografía pero nunca se cita en el cuerpo del texto.",
                elemento=r["titulo"] or "",
                ubicacion="Sección de referencias",
            )
            for r in refs_raw
            if r["ref_id"] not in ids_refs_citadas
        ]

        return {
            "citas_sin_referencia": citas_sin_ref,
            "referencias_sin_citar": refs_sin_citar,
        }

    # ── HU-012: Alertas de alucinaciones del sistema ────────────────────

    def detectar_alucinaciones_sistema(
        self,
        veredictos: list[VeredictoAuditoria],
    ) -> list[AlertaAlucinacionSistema]:
        """
        Filtra los veredictos NO_VERIFICABLE y los convierte en alertas
        explícitas para que el revisor no confíe en esos resultados.
        """
        return [
            AlertaAlucinacionSistema(
                cita_id=v.cita_id,
                texto_cita=v.texto_cita,
                pagina=v.pagina,
                razon_no_verificable=v.justificacion,
            )
            for v in veredictos
            if v.veredicto == VeredictoTipo.NO_VERIFICABLE
        ]

    # ── Internos ─────────────────────────────────────────────────────────

    def _auditar_cita(
        self,
        documento_id: str,
        cita_id: str,
        texto_cita: str,
        fragmento_oracion: str = "",
        pagina: int = 0,
        ref_id: str | None = None,
        ref_titulo: str = "",
        ref_anio: int | None = None,
        autores: list[str] = [],
        doi_verificado: str | None = None,
    ) -> VeredictoAuditoria:
        if not ref_id:
            veredicto = VeredictoAuditoria(
                cita_id=cita_id,
                texto_cita=texto_cita,
                pagina=pagina,
                veredicto=VeredictoTipo.NO_VERIFICABLE,
                justificacion="No se encontró una referencia bibliográfica vinculada a esta cita.",
                metodo_recuperacion="no_encontrado",
                pagina_paper=None,
            )
            self._persistir_veredicto(veredicto, pagina_paper=None)
            return veredicto

        # Saltar si el paper citado no está indexado en Supabase
        if doi_verificado:
            doi_normalizado = doi_verificado.replace("/", "_").replace(".", "_")
            if not supabase_vector_service.paper_ya_indexado(doi_normalizado):
                veredicto = VeredictoAuditoria(
                    cita_id=cita_id,
                    texto_cita=texto_cita,
                    pagina=pagina,
                    veredicto=VeredictoTipo.NO_VERIFICABLE,
                    justificacion="El paper citado no está disponible en la base de conocimiento.",
                    metodo_recuperacion="no_encontrado",
                    pagina_paper=None,
                    fragmento_evidencia="",
                )
                self._persistir_veredicto(veredicto, pagina_paper=None)
                return veredicto

        resultado = recuperacion_service.consultar_cita(
            documento_id=documento_id,
            cita_id=cita_id,
        )

        pagina_paper = resultado.pagina_paper

        logger.debug(
            "cita_auditada_chunk",
            cita_id=cita_id,
            pagina_paper=pagina_paper,
            chunk_index=resultado.chunk_index,
            similitud=resultado.similitud,
        )

        if resultado.metodo in ("no_encontrado", "solo_grafo") or not resultado.fragmento_relevante:
            veredicto = VeredictoAuditoria(
                cita_id=cita_id,
                texto_cita=texto_cita,
                pagina=pagina,
                veredicto=VeredictoTipo.NO_VERIFICABLE,
                justificacion=(
                    "No se encontró texto del paper citado en la base de conocimiento. "
                    "La referencia existe en el grafo pero no hay fragmento disponible para verificar."
                ),
                referencia_id=ref_id,
                titulo_referencia=ref_titulo,
                anio_referencia=ref_anio,
                doi_referencia=resultado.doi_referencia,
                autores_referencia=autores,
                metodo_recuperacion=resultado.metodo,
                pagina_paper=pagina_paper,
            )
            self._persistir_veredicto(veredicto, pagina_paper=pagina_paper)
            return veredicto

        veredicto_tipo, justificacion = self._llamar_llm(
            texto_cita=texto_cita,
            fragmento_oracion=fragmento_oracion,
            fragmento=resultado.fragmento_relevante,
        )

        veredicto = VeredictoAuditoria(
            cita_id=cita_id,
            texto_cita=texto_cita,
            fragmento_oracion=fragmento_oracion,
            pagina=pagina,
            veredicto=veredicto_tipo,
            justificacion=justificacion,
            fragmento_evidencia=resultado.fragmento_relevante,
            similitud=resultado.similitud,
            referencia_id=ref_id,
            titulo_referencia=ref_titulo,
            anio_referencia=ref_anio,
            doi_referencia=resultado.doi_referencia,
            autores_referencia=autores,
            metodo_recuperacion=resultado.metodo,
            pagina_paper=pagina_paper,
        )
        self._persistir_veredicto(veredicto, pagina_paper=pagina_paper)
        return veredicto

    def _llamar_llm(self, texto_cita: str, fragmento_oracion: str, fragmento: str) -> tuple[VeredictoTipo, str]:
        """
        Llama a GPT-4o-mini y parsea el veredicto.
        Si el LLM falla o responde de forma inesperada → NO_VERIFICABLE.
        """
        afirmacion = fragmento_oracion if fragmento_oracion else texto_cita
        prompt = (
            f"AFIRMACIÓN DEL TESISTA: {afirmacion}\n\n"
            f"CITA APA: {texto_cita}\n\n"
            f"FRAGMENTO DEL PAPER: {fragmento}"
        )

        try:
            respuesta = llm_service.completar(
                system_prompt=SYSTEM_AUDITORIA,
                user_prompt=prompt,
            )

            veredicto_tipo, justificacion = self._parsear_respuesta_llm(respuesta)
            return veredicto_tipo, justificacion

        except Exception as e:
            logger.error("error_llm_auditoria", error=str(e))
            return (
                VeredictoTipo.NO_VERIFICABLE,
                "El servicio de verificación no pudo procesar esta cita.",
            )

    @staticmethod
    def _parsear_respuesta_llm(respuesta: str) -> tuple[VeredictoTipo, str]:
        """
        Parsea la respuesta del LLM con el formato:
        VEREDICTO: VÁLIDA|DUDOSA|ALUCINADA
        JUSTIFICACIÓN: ...
        """
        lineas = respuesta.strip().splitlines()
        veredicto_tipo = VeredictoTipo.NO_VERIFICABLE
        justificacion = "No se pudo interpretar la respuesta del verificador."

        for linea in lineas:
            linea = linea.strip()
            if linea.upper().startswith("VEREDICTO:"):
                valor = linea.split(":", 1)[1].strip().upper()
                if "VÁLIDA" in valor or "VALIDA" in valor:
                    veredicto_tipo = VeredictoTipo.VALIDA
                elif "DUDOSA" in valor:
                    veredicto_tipo = VeredictoTipo.DUDOSA
                elif "ALUCINADA" in valor:
                    veredicto_tipo = VeredictoTipo.ALUCINADA

            elif linea.upper().startswith("JUSTIFICACIÓN:") or linea.upper().startswith("JUSTIFICACION:"):
                justificacion = linea.split(":", 1)[1].strip()

        return veredicto_tipo, justificacion

    def _persistir_veredicto(
        self,
        veredicto: VeredictoAuditoria,
        pagina_paper: Optional[int] = None,
    ) -> None:
        """Guarda el veredicto en el nodo Cita de Neo4j."""
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                session.run(
                    _Q_GUARDAR_VEREDICTO,
                    cita_id=veredicto.cita_id,
                    veredicto=veredicto.veredicto.value,
                    justificacion=veredicto.justificacion,
                    pagina_paper=pagina_paper,
                    fragmento_evidencia=veredicto.fragmento_evidencia or "",
                )
        except Exception as e:
            logger.error("error_persistiendo_veredicto", cita_id=veredicto.cita_id, error=str(e))

    # ── EP-RAGAS: Evaluación post-auditoría con RAGAS ──────────────────────

    def evaluar_ragas_documento(self, doc_id: str) -> dict:
        from app.services.evaluacion.ragas_service import ragas_service

        # 1. Leer citas con fragmento_evidencia no vacío
        _Q_LEER_CITAS = """
            MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
            WHERE c.fragmento IS NOT NULL
              AND c.fragmento_evidencia IS NOT NULL
              AND c.fragmento_evidencia <> ''
              AND c.justificacion IS NOT NULL
            RETURN
                c.id                  AS cita_id,
                c.fragmento           AS pregunta,
                c.justificacion       AS respuesta,
                c.fragmento_evidencia AS fragmento_evidencia
        """
        with neo4j_service.driver.session(
            database=settings.neo4j_database
        ) as session:
            resultado = session.run(_Q_LEER_CITAS, doc_id=doc_id)
            citas = [dict(r) for r in resultado]

        if not citas:
            logger.info("ragas_sin_citas_evaluables", doc_id=doc_id)
            return {"total_evaluadas": 0}

        logger.info("ragas_iniciando", doc_id=doc_id, total=len(citas))

        # 2. Evaluar con RAGAS (Neo4j cerrado)
        scores_por_cita = []
        for i, cita in enumerate(citas, 1):
            logger.info(
                "ragas_evaluando_cita",
                numero=i,
                total=len(citas),
            )
            scores = ragas_service.evaluar_cita(
                pregunta   = cita["pregunta"],
                respuesta  = cita["respuesta"],
                contextos  = [cita["fragmento_evidencia"]],
                referencia = cita["fragmento_evidencia"],
            )
            scores_por_cita.append({
                "cita_id": cita["cita_id"],
                **scores
            })

        # 3. Guardar en Neo4j en batch
        _Q_GUARDAR_RAGAS = """
            MATCH (c:Cita {id: $cita_id})
            SET c.faithfulness       = $faithfulness,
                c.answer_relevancy   = $answer_relevancy,
                c.context_precision  = $context_precision,
                c.context_recall     = $context_recall,
                c.answer_correctness = $answer_correctness,
                c.ragas_evaluado_en  = datetime()
        """
        with neo4j_service.driver.session(
            database=settings.neo4j_database
        ) as session:
            for scores in scores_por_cita:
                try:
                    session.run(_Q_GUARDAR_RAGAS, scores)
                except Exception as e:
                    logger.error(
                        "ragas_error_guardando",
                        cita_id=scores["cita_id"],
                        error=str(e)
                    )

        def promedio(key):
            vals = [
                s[key] for s in scores_por_cita
                if s.get(key) is not None
            ]
            return round(sum(vals) / len(vals), 3) if vals else None

        return {
            "total_evaluadas":             len(scores_por_cita),
            "faithfulness_promedio":       promedio("faithfulness"),
            "answer_relevancy_promedio":   promedio("answer_relevancy"),
            "context_precision_promedio":  promedio("context_precision"),
            "context_recall_promedio":     promedio("context_recall"),
            "answer_correctness_promedio": promedio("answer_correctness"),
        }


# Singleton
auditoria_service = AuditoriaService()
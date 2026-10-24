"""
EP-004: Endpoints de Auditoría Semántica

HU-010  POST /{doc_id}/auditar              → auditar todas las citas del documento
HU-010  GET  /{doc_id}/veredictos           → ver veredictos ya calculados
HU-011  GET  /{doc_id}/alertas              → inconsistencias estructurales
HU-012  GET  /{doc_id}/alertas/alucinaciones → citas que el sistema no pudo verificar
"""
import io
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import openpyxl
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.datavalidation import DataValidation
import structlog

from app.core.config import get_settings
from app.services.auditoria.auditoria_service import auditoria_service
from app.services.grafo.neo4j_service import neo4j_service
from app.models.auditoria import (
    VeredictoAuditoria,
    VeredictoTipo,
    AuditoriaResponse,
    AlertasResponse,
    AlertaInconsistencia,
    AlertasAlucinacionResponse,
    MetricasRagasResponse,
)

logger = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/auditoria", tags=["Auditoría"])


def _sin_control(registro) -> dict:
    """Registro de Neo4j como dict, sin caracteres que un .xlsx no admite."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    def limpiar(v):
        if isinstance(v, str):
            return ILLEGAL_CHARACTERS_RE.sub("", v)
        if isinstance(v, list):
            return [limpiar(x) for x in v]
        return v
    return {k: limpiar(v) for k, v in dict(registro).items()}


def _procedencia_resumen(r) -> str:
    """'sistema' | 'humano' | '' para una fila de la bibliografía."""
    nivel = r["nivel_confianza"]
    if r["procedencia_texto"] == "humano" or r["origen_manual"] or nivel == "manual":
        return "humano"
    if r["procedencia_texto"] == "sistema" or nivel in ("texto_completo", "pagina_web"):
        return "sistema"
    return ""


# ── HU-010: Auditar documento ────────────────────────────────────────────────

@router.post(
    "/{documento_id}/auditar",
    response_model=AuditoriaResponse,
    summary="HU-010: Auditar todas las citas del documento",
)
async def auditar_documento(documento_id: str, solo_pendientes: bool = True):
    """
    Ejecuta la auditoría semántica:
    por cada cita recupera evidencia del grafo + Supabase/pgvector
    y emite un veredicto con gpt-5.4-mini.
    Persiste los veredictos en Neo4j.

    Por defecto solo juzga lo que falta: las citas sin veredicto y aquellas cuya
    fuente se obtuvo después de la corrida anterior. En un documento recién
    procesado eso son todas. Con `solo_pendientes=false` se fuerza a reauditar
    el documento entero, que es lo que hay que hacer si cambia el modelo o el
    prompt del juez.
    """
    try:
        veredictos = auditoria_service.auditar_documento(
            documento_id, solo_pendientes=solo_pendientes
        )

        # Los veredictos nuevos invalidan el análisis de coherencia previo.
        try:
            from app.services.coherencia.coherencia_service import coherencia_service
            coherencia_service.invalidar_cache(documento_id)
        except Exception as e:
            logger.warning("coherencia_no_invalidada", doc_id=documento_id, error=str(e))

        if not veredictos:
            # Sin nada pendiente no hay error: es el caso normal al repetir la
            # auditoría sobre un documento que no ha incorporado fuentes nuevas.
            if solo_pendientes:
                logger.info("auditoria_sin_pendientes", doc_id=documento_id)
                return AuditoriaResponse(
                    documento_id=documento_id,
                    total_citas=0, supports=0, refutes=0, no_info=0, veredictos=[],
                    advertencia=("No había citas pendientes: todas conservan el veredicto "
                                 "emitido sobre la misma fuente. Usa solo_pendientes=false "
                                 "para reauditar el documento completo."),
                )
            raise HTTPException(
                status_code=404,
                detail={
                    "codigo": "SIN_CITAS",
                    "mensaje": "No se encontraron citas para auditar en este documento.",
                    "accion_sugerida": "Verifica que la auditoría inicial se haya completado.",
                },
            )

        supports    = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.SUPPORTS)
        refutes     = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.REFUTES)
        no_info     = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.NO_INFO)

        advertencia = None
        if no_info > 0:
            advertencia = (
                f"{no_info} cita(s) no pudieron verificarse por falta de evidencia. "
                f"Revisa las alertas de verificación."
            )

        return AuditoriaResponse(
            documento_id=documento_id,
            total_citas=len(veredictos),
            supports=supports,
            refutes=refutes,
            no_info=no_info,
            veredictos=veredictos,
            advertencia=advertencia,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("error_auditar_documento", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )


# ── HU-010: Ver veredictos ya calculados ─────────────────────────────────────

@router.get(
    "/{documento_id}/veredictos",
    response_model=AuditoriaResponse,
    summary="HU-010: Ver veredictos de auditoría ya calculados",
)
async def ver_veredictos(documento_id: str):
    """
    Lee los veredictos persistidos en Neo4j sin volver a llamar al LLM.
    """
    query = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
    WHERE c.veredicto IS NOT NULL
    OPTIONAL MATCH (c)-[:CITA_A]->(r:Referencia)
    CALL (r) {
  OPTIONAL MATCH (r)-[e:ESCRITO_POR]->(a:Autor)
  WITH a ORDER BY e.orden
  RETURN collect(a.nombre) AS autores
}
    RETURN
      c.id            AS cita_id,
      c.texto         AS texto_cita,
      c.fragmento     AS fragmento_oracion,
      c.pagina        AS pagina,
      c.veredicto     AS veredicto,
      c.justificacion AS justificacion,
      c.pagina_paper  AS pagina_paper,
      c.fragmento_evidencia    AS fragmento_evidencia,
      c.fragmento_evidencia_es AS fragmento_evidencia_es,
      c.cita_textual      AS cita_textual,
      c.seccion_evidencia AS seccion_evidencia,
      c.faithfulness      AS faithfulness,
      c.answer_relevancy  AS answer_relevancy,
      c.context_precision AS context_precision,
      r.id            AS ref_id,
      r.titulo_oficial AS ref_titulo_oficial,
      r.titulo        AS ref_titulo,
      r.anio          AS ref_anio,
      r.doi_verificado AS doi,
      autores
    """
    try:
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            registros = list(session.run(query, doc_id=documento_id))

        if not registros:
            raise HTTPException(
                status_code=404,
                detail={
                    "codigo": "VEREDICTOS_NO_ENCONTRADOS",
                    "mensaje": "No hay veredictos calculados para este documento.",
                    "accion_sugerida": "Ejecuta primero POST /auditoria/{documento_id}/auditar.",
                },
            )

        veredictos = []
        for r in registros:
            try:
                tipo = VeredictoTipo(r["veredicto"])
            except ValueError:
                tipo = VeredictoTipo.NO_INFO

            veredictos.append(VeredictoAuditoria(
                cita_id=r["cita_id"],
                texto_cita=r["texto_cita"] or "",
                fragmento_oracion=r["fragmento_oracion"] or "",
                pagina=r["pagina"] or 0,
                veredicto=tipo,
                justificacion=r["justificacion"] or "",
                fragmento_evidencia=r.get("fragmento_evidencia") or "",
                fragmento_evidencia_es=r.get("fragmento_evidencia_es") or "",
                cita_textual=r.get("cita_textual") or "",
                seccion_evidencia=r.get("seccion_evidencia") or "",
                referencia_id=r["ref_id"],
                titulo_referencia=r["ref_titulo_oficial"] or r["ref_titulo"],
                anio_referencia=r["ref_anio"],
                doi_referencia=r["doi"],
                autores_referencia=r["autores"] or [],
                pagina_paper=r.get("pagina_paper"),
                faithfulness=r.get("faithfulness"),
                answer_relevancy=r.get("answer_relevancy"),
                context_precision=r.get("context_precision"),
            ))

        supports = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.SUPPORTS)
        refutes  = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.REFUTES)
        no_info  = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.NO_INFO)

        return AuditoriaResponse(
            documento_id=documento_id,
            total_citas=len(veredictos),
            supports=supports,
            refutes=refutes,
            no_info=no_info,
            veredictos=veredictos,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("error_ver_veredictos", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )


# ── HU-011: Alertas de inconsistencias estructurales ────────────────────────

@router.get(
    "/{documento_id}/alertas",
    response_model=AlertasResponse,
    summary="HU-011: Ver alertas de citas sin referencia y referencias sin citar",
)
async def ver_alertas(documento_id: str):
    """
    Detecta inconsistencias estructurales:
    - Citas en el cuerpo sin referencia bibliográfica correspondiente
    - Referencias listadas que nunca se citan en el texto
    """
    try:
        resultado = auditoria_service.detectar_inconsistencias(documento_id)

        citas_sin_ref   = resultado["citas_sin_referencia"]
        refs_sin_citar  = resultado["referencias_sin_citar"]
        total           = len(citas_sin_ref) + len(refs_sin_citar)

        if total == 0:
            mensaje = "No se detectaron inconsistencias estructurales. Todas las citas tienen referencia y todas las referencias están citadas."
        else:
            partes = []
            if citas_sin_ref:
                partes.append(f"{len(citas_sin_ref)} cita(s) sin referencia")
            if refs_sin_citar:
                partes.append(f"{len(refs_sin_citar)} referencia(s) sin citar")
            mensaje = "Se detectaron inconsistencias: " + " y ".join(partes) + "."

        return AlertasResponse(
            documento_id=documento_id,
            citas_sin_referencia=citas_sin_ref,
            referencias_sin_citar=refs_sin_citar,
            total_inconsistencias=total,
            mensaje=mensaje,
        )

    except Exception as e:
        logger.error("error_ver_alertas", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )


# ── HU-012: Alertas de alucinaciones del sistema ────────────────────────────

@router.get(
    "/{documento_id}/alertas/alucinaciones",
    response_model=AlertasAlucinacionResponse,
    summary="HU-012: Ver alertas de citas que el sistema no pudo verificar",
)
async def ver_alertas_alucinaciones(documento_id: str):
    """
    Lista las citas con veredicto NO_VERIFICABLE — aquellas para las que
    el sistema no encontró evidencia en el grafo ni en Supabase/pgvector.
    El revisor no debe confiar ciegamente en estos casos.
    """
    query = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
    WHERE c.veredicto = $veredicto
    RETURN c.id AS cita_id, c.texto AS texto_cita,
           c.pagina AS pagina, c.justificacion AS justificacion
    """
    try:
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            registros = list(session.run(
                query,
                doc_id=documento_id,
                veredicto=VeredictoTipo.NO_INFO.value,
            ))

        from app.models.auditoria import AlertaAlucinacionSistema
        alertas = [
            AlertaAlucinacionSistema(
                cita_id=r["cita_id"],
                texto_cita=r["texto_cita"] or "",
                pagina=r["pagina"] or 0,
                razon_no_verificable=r["justificacion"] or "",
            )
            for r in registros
        ]

        advertencia = None
        if alertas:
            advertencia = (
                f"Se encontraron {len(alertas)} cita(s) que el sistema no pudo verificar. "
                f"Estas citas requieren revisión manual."
            )

        return AlertasAlucinacionResponse(
            documento_id=documento_id,
            total_no_info=len(alertas),
            alertas=alertas,
            advertencia=advertencia,
        )

    except Exception as e:
        logger.error("error_alertas_alucinaciones", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )


# ── EP-RAGAS: Evaluación RAGAS del documento ────────────────────────────────

@router.post(
    "/{documento_id}/evaluar-ragas",
    response_model=MetricasRagasResponse,
    summary="EP-RAGAS: Evaluar las citas con fragmento de paper disponible",
)
async def evaluar_ragas(documento_id: str):
    """
    Evalúa con RAGAS las citas que tienen fragmento de evidencia.
    Persiste los scores en cada Cita y retorna los promedios.
    """
    try:
        resultado = auditoria_service.evaluar_ragas_documento(documento_id)

        if resultado.get("total_evaluadas", 0) == 0:
            raise HTTPException(
                status_code=404,
                detail={
                    "codigo": "SIN_EVIDENCIA",
                    "mensaje": (
                        "No hay citas con fragmento de paper disponible "
                        "para evaluar. Verifica que los papers estén indexados."
                    ),
                    "accion_sugerida": "Indexa los papers citados y vuelve a auditar.",
                },
            )

        return MetricasRagasResponse(**resultado)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("error_evaluar_ragas", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )


@router.get(
    "/{documento_id}/metricas",
    response_model=MetricasRagasResponse,
    summary="EP-RAGAS: Ver métricas RAGAS promedio del documento",
)
async def ver_metricas(documento_id: str):
    """
    Lee los scores RAGAS ya calculados en cada Cita y retorna
    los promedios del documento.
    """
    query = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
    WHERE c.faithfulness IS NOT NULL
    RETURN
      avg(c.faithfulness)      AS faithfulness_promedio,
      avg(c.answer_relevancy)  AS answer_relevancy_promedio,
      avg(c.context_precision) AS context_precision_promedio,
      count(c)                 AS total_citas_evaluadas
    """
    try:
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            registro = session.run(query, doc_id=documento_id).single()

        total = registro["total_citas_evaluadas"] if registro else 0
        if total == 0:
            raise HTTPException(
                status_code=404,
                detail={
                    "codigo": "METRICAS_NO_ENCONTRADAS",
                    "mensaje": "No hay métricas RAGAS calculadas para este documento.",
                    "accion_sugerida": "Ejecuta primero POST /auditoria/{documento_id}/evaluar-ragas.",
                },
            )

        def _redondear(v):
            return round(float(v), 3) if v is not None else None

        return MetricasRagasResponse(
            total_evaluadas=total,
            faithfulness_promedio=_redondear(registro["faithfulness_promedio"]),
            answer_relevancy_promedio=_redondear(registro["answer_relevancy_promedio"]),
            context_precision_promedio=_redondear(registro["context_precision_promedio"]),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("error_ver_metricas", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )


# ── Exportar informe completo a Excel ───────────────────────────────────────

@router.get(
    "/{documento_id}/metricas/exportar",
    summary="Exportar informe completo de auditoría a Excel",
)
async def exportar_metricas_excel(documento_id: str, incluir_ragas: bool = False):
    """
    Genera un Excel con dos hojas:
    - Resumen: estadísticas generales del documento
    - Citas:   detalle por cita con veredicto y fragmentos

    Las métricas RAGAS (calidad interna del sistema) NO van en el informe del
    usuario; solo se incluyen con ?incluir_ragas=true (vista de administración).
    """
    _Q_CITAS_DETALLE = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
    OPTIONAL MATCH (c)-[rel:CITA_A]->(r:Referencia)
    CALL (r) {
        OPTIONAL MATCH (r)-[e:ESCRITO_POR]->(a:Autor)
        WITH a ORDER BY e.orden
        RETURN collect(a.nombre) AS autores
    }
    RETURN
      c.id                 AS cita_id,
      c.etiqueta_experto   AS etiqueta_experto,
      c.justificacion_experto AS justificacion_experto,
      rel.confianza        AS confianza_vinculo,
      rel.metodo           AS metodo_vinculo,
      rel.ambigua          AS vinculo_ambiguo,
      c.texto              AS texto_cita,
      c.fragmento          AS fragmento_oracion,
      c.pagina             AS pagina_tesis,
      c.veredicto          AS veredicto,
      c.justificacion      AS justificacion,
      c.fragmento_evidencia AS fragmento_paper,
      c.fragmento_evidencia_es AS fragmento_paper_es,
      c.fragmento_legible  AS fragmento_legible,
      c.cita_textual       AS cita_textual,
      c.seccion_evidencia  AS seccion_evidencia,
      c.contradiccion      AS contradiccion,
      c.similitud          AS similitud,
      c.clase_cita         AS clase_cita,
      c.tipo               AS tipo_cita,
      c.pagina_paper       AS pagina_paper,
      c.faithfulness       AS faithfulness,
      c.answer_relevancy   AS answer_relevancy,
      c.context_precision  AS context_precision,
      r.titulo_oficial     AS titulo_oficial,
      r.titulo             AS titulo_referencia,
      r.anio               AS anio_referencia,
      r.doi_verificado     AS doi_referencia,
      r.nivel_confianza    AS nivel_confianza,
      r.motivo_sin_texto   AS motivo_sin_texto,
      r.procedencia_texto  AS procedencia_texto,
      r.origen_texto       AS origen_texto,
      r.origen_manual      AS origen_manual_ref,
      autores
    ORDER BY c.pagina
    """

    # Toda la bibliografía con lo que el sistema averiguó de cada entrada:
    # identificación, disponibilidad del texto, motivo si no se obtuvo, y
    # cuántas citas la referencian.
    _Q_BIBLIOGRAFIA = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
    CALL (r) {
        OPTIONAL MATCH (r)-[e:ESCRITO_POR]->(a:Autor)
        WITH a ORDER BY e.orden
        RETURN collect(a.nombre) AS autores
    }
    OPTIONAL MATCH (c:Cita)-[:CITA_A]->(r)
    OPTIONAL MATCH (r)-[:CITA_EXTERNA]->(x:ReferenciaExterna)
    RETURN
      r.id                      AS ref_id,
      autores,
      r.anio                    AS anio,
      r.titulo                  AS titulo,
      r.titulo_oficial          AS titulo_oficial,
      r.doi                     AS doi_declarado,
      r.doi_verificado          AS doi_verificado,
      r.doi_resuelto_por_titulo AS doi_por_titulo,
      r.nivel_confianza         AS nivel_confianza,
      r.motivo_sin_texto        AS motivo_sin_texto,
      r.origen_manual           AS origen_manual,
      r.nota_manual             AS nota_manual,
      r.procedencia_texto       AS procedencia_texto,
      r.origen_texto            AS origen_texto,
      coalesce(r.url_texto, r.url_usada, r.url) AS url_texto,
      r.alerta_referencia       AS alerta_referencia,
      r.datos_incompletos       AS datos_incompletos,
      count(DISTINCT c)         AS veces_citada,
      count(DISTINCT x)         AS refs_externas
    ORDER BY toLower(coalesce(r.titulo, ''))
    """

    _Q_REFERENCIAS = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
    RETURN count(r) AS total_referencias,
           sum(CASE WHEN r.nivel_confianza IS NOT NULL AND r.nivel_confianza <> 'no_encontrado' THEN 1 ELSE 0 END) AS con_texto
    """

    _Q_REFS_SIN_CITAR = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
    WHERE NOT (r)<-[:CITA_A]-()
    RETURN count(r) AS total
    """

    _Q_CITAS_SIN_REF = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
    WHERE NOT (c)-[:CITA_A]->()
    RETURN count(c) AS total
    """

    _Q_RAGAS_PROMEDIO = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
    WHERE c.faithfulness IS NOT NULL
    RETURN
      count(c)               AS total_evaluadas,
      avg(c.faithfulness)    AS faithfulness_avg,
      avg(c.answer_relevancy) AS relevancy_avg,
      avg(c.context_precision) AS precision_avg
    """

    try:
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            # El texto de los PDF arrastra caracteres de control (restos de
            # glifos: \x14, \x15…) que openpyxl rechaza; uno solo en un
            # fragmento de evidencia tumbaba el informe entero.
            citas_raw   = [_sin_control(r) for r in session.run(_Q_CITAS_DETALLE, doc_id=documento_id)]
            biblio_raw  = [_sin_control(r) for r in session.run(_Q_BIBLIOGRAFIA, doc_id=documento_id)]
            refs_raw    = session.run(_Q_REFERENCIAS, doc_id=documento_id).single()
            doc_raw     = session.run(
                "MATCH (d:Documento {id: $doc_id}) "
                "RETURN d.titulo AS titulo, d.nombre_archivo AS nombre",
                doc_id=documento_id,
            ).single()
            sin_citar   = session.run(_Q_REFS_SIN_CITAR, doc_id=documento_id).single()
            sin_ref     = session.run(_Q_CITAS_SIN_REF, doc_id=documento_id).single()
            ragas_prom  = session.run(_Q_RAGAS_PROMEDIO, doc_id=documento_id).single()

        # Título de la tesis para la columna de la plantilla: el nombre del
        # archivo sin la extensión es lo más cercano que tiene el sistema.
        nombre_documento = (
            (doc_raw["titulo"] or doc_raw["nombre"]) if doc_raw else ""
        ) or documento_id
        if nombre_documento.lower().endswith(".pdf"):
            nombre_documento = nombre_documento[:-4]

        if not citas_raw:
            raise HTTPException(
                status_code=404,
                detail={
                    "codigo": "CITAS_NO_ENCONTRADAS",
                    "mensaje": "No hay citas auditadas para exportar.",
                    "accion_sugerida": "Ejecuta primero POST /auditoria/{documento_id}/auditar.",
                },
            )

        def _r(v):
            return round(float(v), 3) if v is not None else None

        def _s(v):
            # El texto extraído de PDFs puede traer caracteres de control
            # ilegales para XML; openpyxl revienta al guardar si no se sanean.
            return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v

        # ── Estilos compartidos ──────────────────────────────────────────────
        header_fill = PatternFill("solid", fgColor="1E293B")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        section_fill = PatternFill("solid", fgColor="334155")
        section_font = Font(bold=True, color="CBD5E1", size=10)
        center = Alignment(horizontal="center", vertical="center")
        wrap   = Alignment(wrap_text=True, vertical="top")

        FILL_SUPPORTS = PatternFill("solid", fgColor="D1FAE5")
        FILL_REFUTES  = PatternFill("solid", fgColor="FEE2E2")
        FILL_NO_INFO  = PatternFill("solid", fgColor="F1F5F9")

        wb = openpyxl.Workbook()

        # ── Hoja 1: Resumen ──────────────────────────────────────────────────
        ws_res = wb.active
        ws_res.title = "Resumen"

        def _escribir_seccion(ws, fila, titulo):
            c = ws.cell(row=fila, column=1, value=titulo)
            c.fill = section_fill
            c.font = section_font
            c.alignment = center
            ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=2)
            ws.row_dimensions[fila].height = 20
            return fila + 1

        def _escribir_fila(ws, fila, etiqueta, valor):
            ws.cell(row=fila, column=1, value=etiqueta).font = Font(bold=False, size=10)
            ws.cell(row=fila, column=2, value=valor).alignment = center
            return fila + 1

        # Portada del resumen
        c = ws_res.cell(row=1, column=1, value="INFORME DE AUDITORÍA DE CITAS")
        c.font = Font(bold=True, size=14, color="1E293B")
        ws_res.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2)
        ws_res.row_dimensions[1].height = 26
        from datetime import datetime
        c = ws_res.cell(row=2, column=1, value=f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} · GraphAuditor")
        c.font = Font(size=9, color="64748B")
        ws_res.merge_cells(start_row=2, start_column=1, end_row=2, end_column=2)

        fila = 4
        fila = _escribir_seccion(ws_res, fila, "REFERENCIAS")
        total_refs = refs_raw["total_referencias"] if refs_raw else 0
        # «Con texto» contaba antes toda referencia verificada, incluidas las
        # que solo tienen resumen o ningún texto: el informe inflaba la
        # cobertura. Se desglosa lo que el juez puede usar de verdad.
        # Qué parte del texto completo logró el sistema solo y qué parte exigió
        # buscar y subir el paper a mano: son las dos versiones del informe.
        _proc = [_procedencia_resumen(r) for r in biblio_raw]
        con_completo = _proc.count("sistema") + _proc.count("humano")
        solo_resumen = sum(1 for r in biblio_raw if r["nivel_confianza"] == "abstract")
        fila = _escribir_fila(ws_res, fila, "Total referencias bibliográficas", total_refs)
        fila = _escribir_fila(ws_res, fila, "Referencias con texto completo", con_completo)
        fila = _escribir_fila(ws_res, fila, "  · conseguido por el sistema", _proc.count("sistema"))
        fila = _escribir_fila(ws_res, fila, "  · aportado por intervención humana", _proc.count("humano"))
        fila = _escribir_fila(ws_res, fila, "Referencias solo con resumen (abstract)", solo_resumen)
        fila = _escribir_fila(ws_res, fila, "Referencias sin texto", total_refs - con_completo - solo_resumen)
        fila = _escribir_fila(ws_res, fila, "Referencias sin citar en las secciones auditadas", sin_citar["total"] if sin_citar else 0)

        fila += 1
        fila = _escribir_seccion(ws_res, fila, "CITAS")
        total_citas   = len(citas_raw)
        supports_cnt  = sum(1 for r in citas_raw if r["veredicto"] == "SUPPORTS")
        refutes_cnt   = sum(1 for r in citas_raw if r["veredicto"] == "REFUTES")
        no_info_cnt   = sum(1 for r in citas_raw if r["veredicto"] == "NO_INFO")
        sin_ref_cnt   = sin_ref["total"] if sin_ref else 0
        fila = _escribir_fila(ws_res, fila, "Total citas identificadas", total_citas)
        fila = _escribir_fila(ws_res, fila, "Citas sin referencia bibliográfica", sin_ref_cnt)
        fila = _escribir_fila(ws_res, fila, "Citas respaldadas por su fuente", supports_cnt)
        fila = _escribir_fila(ws_res, fila, "Citas refutadas por su fuente", refutes_cnt)
        fila = _escribir_fila(ws_res, fila, "Citas sin evidencia verificable", no_info_cnt)

        # «Sin evidencia verificable» no es un solo fenómeno: mezcla las citas
        # que el juez leyó contra la fuente completa y no encontró respaldo con
        # aquellas cuya fuente nunca se consiguió. Sin separarlas se le atribuye
        # al tesista lo que en realidad es falta de acceso al documento.
        def _estado_fuente(r) -> str:
            nivel = (r["nivel_confianza"] or "").strip()
            if not nivel:
                return "sin_referencia"
            if nivel == "texto_completo":
                return "texto_completo"
            if nivel in ("abstract", "pagina_web"):
                return "texto_parcial"
            return "sin_texto"

        estados = [(_estado_fuente(r), r["veredicto"]) for r in citas_raw]
        _n = lambda est, ver=None: sum(
            1 for e, v in estados if e == est and (ver is None or v == ver)
        )
        fila = _escribir_fila(ws_res, fila, "  · con el texto completo de la fuente",
                              _n("texto_completo", "NO_INFO"))
        fila = _escribir_fila(ws_res, fila, "  · con solo resumen o página web",
                              _n("texto_parcial", "NO_INFO"))
        fila = _escribir_fila(ws_res, fila, "  · sin texto de la fuente",
                              _n("sin_texto", "NO_INFO"))
        fila = _escribir_fila(ws_res, fila, "  · sin referencia enlazada",
                              _n("sin_referencia", "NO_INFO"))

        fila += 1
        fila = _escribir_seccion(ws_res, fila, "CITAS VERIFICADAS CONTRA EL TEXTO COMPLETO")
        completo = _n("texto_completo")
        fila = _escribir_fila(ws_res, fila, "Citas con el texto completo de su fuente", completo)
        fila = _escribir_fila(ws_res, fila, "  · respaldadas (SUPPORTS)", _n("texto_completo", "SUPPORTS"))
        fila = _escribir_fila(ws_res, fila, "  · refutadas (REFUTES)", _n("texto_completo", "REFUTES"))
        fila = _escribir_fila(ws_res, fila, "  · sin información suficiente (NO INFO)",
                              _n("texto_completo", "NO_INFO"))
        fila = _escribir_fila(
            ws_res, fila, "Cobertura de auditoría",
            f"{completo / total_citas:.1%}" if total_citas else "—",
        )

        # Métricas RAGAS: solo en la exportación de administración (D9).
        if incluir_ragas:
            fila += 1
            total_ev = ragas_prom["total_evaluadas"] if ragas_prom else 0
            if total_ev:
                fila = _escribir_seccion(ws_res, fila, "MÉTRICAS RAGAS (uso interno)")
                fila = _escribir_fila(ws_res, fila, "Citas evaluadas con RAGAS", int(total_ev))
                fila = _escribir_fila(ws_res, fila, "Context Precision (promedio)", _r(ragas_prom["precision_avg"]))

        ws_res.column_dimensions["A"].width = 42
        ws_res.column_dimensions["B"].width = 16

        # ── Hoja 2: Citas ────────────────────────────────────────────────────

        # ── Hoja "Auditoría": el formato de revisión, una fila por cita ──────
        # Replica exactamente la plantilla que usa el equipo evaluador, para
        # que las filas se puedan pegar sin retocar nada. Las columnas propias
        # del sistema van DESPUÉS de las suyas, para no romper ese pegado.
        from app.services.externo.unpaywall_service import describir_motivo

        def _procedencia(nivel, procedencia, manual) -> str:
            """Quién consiguió el texto de la fuente: el sistema por su cuenta
            o una persona que lo buscó y lo subió. El informe v1 (solo sistema)
            y el v2 (con intervención humana) se distinguen por esta columna."""
            if procedencia == "humano" or manual or nivel == "manual":
                return "Intervención humana"
            if procedencia == "sistema" or nivel in ("texto_completo", "pagina_web", "abstract"):
                return "Sistema (automático)"
            return ""

        ws_aud = wb.create_sheet("Auditoría", 1)
        cols_plantilla = [
            ("N°", 6),
            ("TESIS\n(Título)", 38),
            ("REFERENCIA CITADA\n(Autor, Año)", 40),
            ("AFIRMACIÓN DEL TESISTA\n(Texto extraído de la tesis)", 55),
            ("FRAGMENTO DEL PAPER\n(Texto original — es lo que evaluó el LLM)", 60),
            ("CLASIFICACIÓN\n(SUPPORTS / REFUTES / NO INFO)", 18),
            ("JUSTIFICACIÓN\n(Motivo de la clasificación)", 55),
            ("NOMBRE EVALUADOR", 20),
        ]
        # Agrupadas por bloques: primero lo que sostiene el veredicto, luego
        # dónde está cada cosa, después cómo se vinculó la cita y por último
        # el estado de la fuente. Sueltas y sin orden no se leen.
        # Solo la evidencia que acompaña al veredicto. Lo diagnóstico —
        # similitudes, método de vínculo, estado de la fuente— vive en la hoja
        # "Detalle técnico": mezclarlo aquí convertía la hoja en algo
        # inmanejable de leer.
        cols_sistema = [
            ("Fragmento en español (legible)", 60),
            ("Frase literal que sostiene el veredicto", 55),
            ("Qué afirma la fuente (solo REFUTES)", 50),
            ("Pág. tesis", 10),
            ("Pág. paper", 10),
            ("Procedencia del paper", 22),
        ]
        for i, (titulo, ancho) in enumerate(cols_plantilla + cols_sistema, start=1):
            c = ws_aud.cell(row=1, column=i, value=titulo)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws_aud.column_dimensions[c.column_letter].width = ancho
        ws_aud.row_dimensions[1].height = 34
        ws_aud.freeze_panes = "A2"

        etiqueta_seccion = {
            "resumen": "resumen", "introduccion": "introducción",
            "trabajos_relacionados": "trabajos relacionados", "metodos": "métodos",
            "resultados": "resultados", "discusion": "discusión",
            "conclusion": "conclusión", "otro": "no identificada",
        }

        for n, r in enumerate(citas_raw, start=1):
            autores = r["autores"] or []
            apellido = autores[0].split(",")[0] if autores else ""
            titulo_fuente = r["titulo_oficial"] or r["titulo_referencia"] or ""
            # Misma forma que usa la plantilla: la cita y, a continuación, el
            # título del paper.
            referencia = f"{r['texto_cita']} {titulo_fuente}".strip()
            original = r["fragmento_paper"] or ""

            fila = [
                n,
                nombre_documento,
                referencia,
                r["fragmento_oracion"] or "",
                original,
                (r["veredicto"] or "").replace("NO_INFO", "NO INFO"),
                r["justificacion"] or "",
                "Sistema GraphRAG Auditor",
                r["fragmento_legible"] or r["fragmento_paper_es"] or "",
                r["cita_textual"] or "",
                r["contradiccion"] or "",
                r["pagina_tesis"] or "",
                r["pagina_paper"] if r["pagina_paper"] is not None else "",
                _procedencia(r["nivel_confianza"], r["procedencia_texto"], r["origen_manual_ref"]),
            ]
            for col, valor in enumerate(fila, start=1):
                celda = ws_aud.cell(row=n + 1, column=col, value=valor)
                celda.alignment = Alignment(vertical="top", wrap_text=col > 1)
            ws_aud.cell(row=n + 1, column=6).fill = {
                "SUPPORTS": FILL_SUPPORTS, "REFUTES": FILL_REFUTES,
            }.get(r["veredicto"], FILL_NO_INFO)

        ws_aud.auto_filter.ref = (
            f"A1:{ws_aud.cell(row=1, column=len(cols_plantilla) + len(cols_sistema)).column_letter}"
            f"{max(len(citas_raw) + 1, 2)}"
        )

        # ── Hoja "Revisión" ─────────────────────────────────────────────────
        # Es la hoja que el experto rellena y que vuelve al sistema. Lleva solo
        # lo imprescindible para decidir —la afirmación, la evidencia y lo que
        # dictaminó el sistema— porque pedirle a un revisor que marque una
        # casilla navegando veinte columnas es garantía de que no lo haga.
        ws_rev = wb.create_sheet("Revisión", 1)
        cols_rev = [
            ("N°", 6),
            ("CITA", 26),
            ("AFIRMACIÓN DEL TESISTA", 58),
            ("EVIDENCIA DEL PAPER\n(en español)", 62),
            ("FRASE LITERAL DEL PAPER", 50),
            ("VEREDICTO DEL SISTEMA", 18),
            ("JUSTIFICACIÓN DEL SISTEMA", 55),
            ("TU VEREDICTO\n(elige de la lista)", 22),
            ("TU JUSTIFICACIÓN\n(por qué discrepas o confirmas)", 55),
            ("cita_id (no modificar)", 38),
        ]
        for i, (titulo, ancho) in enumerate(cols_rev, start=1):
            c = ws_rev.cell(row=1, column=i, value=titulo)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws_rev.column_dimensions[c.column_letter].width = ancho
        ws_rev.row_dimensions[1].height = 34
        # Congelar hasta la justificación del sistema: al desplazarse a la
        # derecha para escribir, el revisor sigue viendo qué está juzgando.
        ws_rev.freeze_panes = "H2"

        relleno_revisar = PatternFill("solid", fgColor="FEF9C3")
        for n, r in enumerate(citas_raw, start=1):
            fila = [
                n,
                r["texto_cita"] or "",
                r["fragmento_oracion"] or "",
                r["fragmento_legible"] or r["fragmento_paper_es"] or r["fragmento_paper"] or "",
                r["cita_textual"] or "",
                (r["veredicto"] or "").replace("NO_INFO", "NO INFO"),
                r["justificacion"] or "",
                (r["etiqueta_experto"] or "").replace("NO_INFO", "NO INFO"),
                r["justificacion_experto"] or "",
                r["cita_id"],
            ]
            for col, valor in enumerate(fila, start=1):
                celda = ws_rev.cell(row=n + 1, column=col, value=valor)
                celda.alignment = Alignment(vertical="top", wrap_text=col in (2, 3, 4, 5, 7, 9))
            ws_rev.cell(row=n + 1, column=6).fill = {
                "SUPPORTS": FILL_SUPPORTS, "REFUTES": FILL_REFUTES,
            }.get(r["veredicto"], FILL_NO_INFO)
            # Las dos columnas a rellenar, resaltadas.
            for col in (8, 9):
                ws_rev.cell(row=n + 1, column=col).fill = relleno_revisar

        # Desplegable: evita erratas que luego romperían el emparejamiento.
        validacion = DataValidation(
            type="list", formula1='"SUPPORTS,REFUTES,NO INFO"',
            allow_blank=True, showDropDown=False,
        )
        validacion.error = "Usa SUPPORTS, REFUTES o NO INFO."
        validacion.errorTitle = "Valor no válido"
        ws_rev.add_data_validation(validacion)
        validacion.add(f"H2:H{max(len(citas_raw) + 1, 2)}")
        ws_rev.auto_filter.ref = f"A1:J{max(len(citas_raw) + 1, 2)}"

        # ── Hoja "Detalle técnico" ──────────────────────────────────────────
        # Para diagnosticar cuando un veredicto no cuadra: de dónde salió la
        # evidencia, cómo se emparejó la cita y en qué estado está la fuente.
        ws_det = wb.create_sheet("Detalle técnico")
        cols_det = [
            ("N°", 6), ("Cita", 26), ("Similitud", 11), ("Sección del paper", 18),
            ("Tipo de cita", 14), ("Clase de cita", 22), ("Vínculo: método", 24),
            ("Vínculo: confianza", 16), ("Vínculo ambiguo", 15),
            ("DOI de la fuente", 30), ("Disponibilidad del paper", 18),
            ("Motivo si no hay texto", 50),
            ("Procedencia del paper", 20), ("Fuente del texto", 18),
            ("cita_id", 38),
        ]
        if incluir_ragas:
            cols_det.append(("RAGAS: pertinencia del fragmento", 22))
        for i, (titulo, ancho) in enumerate(cols_det, start=1):
            c = ws_det.cell(row=1, column=i, value=titulo)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws_det.column_dimensions[c.column_letter].width = ancho
        ws_det.freeze_panes = "A2"

        for n, r in enumerate(citas_raw, start=1):
            fila = [
                n, r["texto_cita"] or "",
                round(r["similitud"], 3) if r["similitud"] else "",
                etiqueta_seccion.get(r["seccion_evidencia"] or "", ""),
                r["tipo_cita"] or "", r["clase_cita"] or "",
                r["metodo_vinculo"] or "",
                r["confianza_vinculo"] if r["confianza_vinculo"] else "",
                "sí" if r["vinculo_ambiguo"] else "",
                r["doi_referencia"] or "", r["nivel_confianza"] or "",
                describir_motivo(r["motivo_sin_texto"]),
                _procedencia(r["nivel_confianza"], r["procedencia_texto"], r["origen_manual_ref"]),
                r["origen_texto"] or "",
                r["cita_id"],
            ]
            if incluir_ragas:
                fila.append(r["context_precision"])
            for col, valor in enumerate(fila, start=1):
                ws_det.cell(row=n + 1, column=col, value=valor).alignment = Alignment(
                    vertical="top", wrap_text=col in (2, 12)
                )
        ws_det.auto_filter.ref = (
            f"A1:{ws_det.cell(row=1, column=len(cols_det)).column_letter}"
            f"{max(len(citas_raw) + 1, 2)}"
        )

        # ── Hoja "Bibliografía" ─────────────────────────────────────────────
        ws_bib = wb.create_sheet("Bibliografía")
        cols_bib = [
            ("N°", 6), ("Autores (en orden)", 42), ("Año", 8),
            ("Título en la tesis", 55), ("Título oficial (CrossRef)", 55),
            ("DOI declarado en la tesis", 30), ("DOI verificado", 30),
            ("DOI resuelto por título", 18), ("Disponibilidad", 18),
            ("Motivo si no hay texto", 55),
            ("Procedencia del texto", 20), ("Nota", 55),
            ("Veces citada", 12),
            ("Referencias del paper en el grafo", 16), ("Datos incompletos", 16),
            ("Fuente del texto", 18), ("URL usada", 45), ("Alerta sobre la referencia", 55),
        ]
        for i, (titulo, ancho) in enumerate(cols_bib, start=1):
            c = ws_bib.cell(row=1, column=i, value=titulo)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws_bib.column_dimensions[c.column_letter].width = ancho
        ws_bib.freeze_panes = "A2"

        for n, r in enumerate(biblio_raw, start=1):
            fila = [
                n,
                "; ".join(r["autores"] or []),
                r["anio"] or "",
                r["titulo"] or "",
                r["titulo_oficial"] or "",
                r["doi_declarado"] or "",
                r["doi_verificado"] or "",
                "sí (confirmar)" if r["doi_por_titulo"] else "",
                r["nivel_confianza"] or "no verificada",
                describir_motivo(r["motivo_sin_texto"]),
                _procedencia(r["nivel_confianza"], r["procedencia_texto"], r["origen_manual"]),
                r["nota_manual"] or "",
                r["veces_citada"],
                r["refs_externas"],
                "sí" if r["datos_incompletos"] else "",
                r["origen_texto"] or "",
                r["url_texto"] or "",
                describir_motivo(r["alerta_referencia"]),
            ]
            for col, valor in enumerate(fila, start=1):
                celda = ws_bib.cell(row=n + 1, column=col, value=valor)
                celda.alignment = Alignment(vertical="top", wrap_text=col in (2, 4, 5, 10, 17, 18))
            if not r["veces_citada"]:
                ws_bib.cell(row=n + 1, column=13).fill = FILL_REFUTES
        ws_bib.auto_filter.ref = f"A1:{ws_bib.cell(row=1, column=len(cols_bib)).column_letter}{max(len(biblio_raw) + 1, 2)}"

        # ── Hoja "Alertas" ──────────────────────────────────────────────────
        ws_al = wb.create_sheet("Alertas")
        for i, (titulo, ancho) in enumerate(
            [("Tipo", 30), ("Elemento", 70), ("Detalle", 70)], start=1
        ):
            c = ws_al.cell(row=1, column=i, value=titulo)
            c.fill = header_fill
            c.font = header_font
            ws_al.column_dimensions[c.column_letter].width = ancho
        ws_al.freeze_panes = "A2"

        alertas: list[tuple[str, str, str]] = []
        for r in citas_raw:
            if not r["titulo_referencia"] and not r["titulo_oficial"]:
                alertas.append((
                    "Cita sin referencia", r["texto_cita"] or "",
                    f"Página {r['pagina_tesis'] or '?'} de la tesis. "
                    "No hay una entrada en la bibliografía que le corresponda.",
                ))
            if r["vinculo_ambiguo"]:
                alertas.append((
                    "Vinculación ambigua", r["texto_cita"] or "",
                    "Más de una referencia encaja igual de bien. Confirma cuál es.",
                ))
        for r in biblio_raw:
            if not r["veces_citada"]:
                alertas.append((
                    "Referencia nunca citada", r["titulo"] or "",
                    "Está en la bibliografía pero no se cita en las secciones auditadas. "
                    "Puede citarse solo en metodología, resultados o conclusiones, que no se auditan.",
                ))
            if r["alerta_referencia"] in ("doi_no_corresponde", "url_rota", "url_generica"):
                alertas.append((
                    {"doi_no_corresponde": "DOI de otro trabajo", "url_rota": "URL rota",
                     "url_generica": "URL genérica"}[r["alerta_referencia"]],
                    r["titulo"] or "",
                    describir_motivo(r["alerta_referencia"])
                    + (f" URL: {r['url_texto']}" if r["url_texto"] and r["alerta_referencia"] != "doi_no_corresponde" else "")
                    + (f" DOI escrito: {r['doi_declarado']}" if r["alerta_referencia"] == "doi_no_corresponde" else ""),
                ))
            if r["motivo_sin_texto"] == "doi_inexistente":
                alertas.append((
                    "DOI inexistente", r["titulo"] or "",
                    describir_motivo(r["motivo_sin_texto"]),
                ))
        for n, (tipo, elemento, detalle) in enumerate(alertas, start=2):
            ws_al.cell(row=n, column=1, value=tipo)
            ws_al.cell(row=n, column=2, value=elemento).alignment = Alignment(wrap_text=True, vertical="top")
            ws_al.cell(row=n, column=3, value=detalle).alignment = Alignment(wrap_text=True, vertical="top")

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        filename = f"informe_{documento_id}.xlsx"
        headers = {"Content-Disposition": f"attachment; filename={filename}"}

        logger.info("exportar_informe_excel", doc_id=documento_id, total_citas=len(citas_raw))
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("error_exportar_informe", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )
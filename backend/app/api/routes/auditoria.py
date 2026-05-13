"""
EP-004: Endpoints de Auditoría Semántica

HU-010  POST /{doc_id}/auditar              → auditar todas las citas del documento
HU-010  GET  /{doc_id}/veredictos           → ver veredictos ya calculados
HU-011  GET  /{doc_id}/alertas              → inconsistencias estructurales
HU-012  GET  /{doc_id}/alertas/alucinaciones → citas que el sistema no pudo verificar
"""
from fastapi import APIRouter, HTTPException
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
)

logger = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/auditoria", tags=["Auditoría"])


# ── HU-010: Auditar documento ────────────────────────────────────────────────

@router.post(
    "/{documento_id}/auditar",
    response_model=AuditoriaResponse,
    summary="HU-010: Auditar todas las citas del documento",
)
async def auditar_documento(documento_id: str):
    """
    Ejecuta la auditoría semántica completa:
    por cada cita recupera evidencia del grafo + ChromaDB
    y emite un veredicto con GPT-4o-mini.
    Persiste los veredictos en Neo4j.
    """
    try:
        veredictos = auditoria_service.auditar_documento(documento_id)

        if not veredictos:
            raise HTTPException(
                status_code=404,
                detail={
                    "codigo": "SIN_CITAS",
                    "mensaje": "No se encontraron citas para auditar en este documento.",
                    "accion_sugerida": "Verifica que la auditoría inicial se haya completado.",
                },
            )

        validas        = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.VALIDA)
        dudosas        = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.DUDOSA)
        alucinadas     = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.ALUCINADA)
        no_verificables = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.NO_VERIFICABLE)

        advertencia = None
        if no_verificables > 0:
            advertencia = (
                f"{no_verificables} cita(s) no pudieron verificarse por falta de evidencia. "
                f"Revisa las alertas de alucinación del sistema."
            )

        return AuditoriaResponse(
            documento_id=documento_id,
            total_citas=len(veredictos),
            validas=validas,
            dudosas=dudosas,
            alucinadas=alucinadas,
            no_verificables=no_verificables,
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
    OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
    RETURN
      c.id            AS cita_id,
      c.texto         AS texto_cita,
      c.fragmento     AS fragmento_oracion,
      c.pagina        AS pagina,
      c.veredicto     AS veredicto,
      c.justificacion AS justificacion,
      r.id            AS ref_id,
      r.titulo_oficial AS ref_titulo_oficial,
      r.titulo        AS ref_titulo,
      r.anio          AS ref_anio,
      r.doi_verificado AS doi,
      collect(DISTINCT a.nombre) AS autores
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
                tipo = VeredictoTipo.NO_VERIFICABLE

            veredictos.append(VeredictoAuditoria(
                cita_id=r["cita_id"],
                texto_cita=r["texto_cita"] or "",
                fragmento_oracion=r["fragmento_oracion"] or "",
                pagina=r["pagina"] or 0,
                veredicto=tipo,
                justificacion=r["justificacion"] or "",
                referencia_id=r["ref_id"],
                titulo_referencia=r["ref_titulo_oficial"] or r["ref_titulo"],
                anio_referencia=r["ref_anio"],
                doi_referencia=r["doi"],
                autores_referencia=r["autores"] or [],
            ))

        validas         = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.VALIDA)
        dudosas         = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.DUDOSA)
        alucinadas      = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.ALUCINADA)
        no_verificables = sum(1 for v in veredictos if v.veredicto == VeredictoTipo.NO_VERIFICABLE)

        return AuditoriaResponse(
            documento_id=documento_id,
            total_citas=len(veredictos),
            validas=validas,
            dudosas=dudosas,
            alucinadas=alucinadas,
            no_verificables=no_verificables,
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
    el sistema no encontró evidencia en el grafo ni en ChromaDB.
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
                veredicto=VeredictoTipo.NO_VERIFICABLE.value,
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
            total_no_verificables=len(alertas),
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
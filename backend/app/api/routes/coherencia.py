"""
Módulo de Coherencia Inter-Fuentes (diseño: docs/COHERENCIA.md).

POST /{doc_id}           → construye el análisis (conceptos + juez de aristas)
GET  /{doc_id}           → hallazgos + mapa (del cache en Storage)
GET  /{doc_id}/grafo     → solo el mapa de referencias para la visualización
"""
from fastapi import APIRouter, HTTPException
import structlog

from app.models.coherencia import CoherenciaResponse, MapaCoherencia
from app.services.coherencia.coherencia_service import coherencia_service

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/coherencia", tags=["Coherencia"])


@router.post(
    "/{documento_id}",
    response_model=CoherenciaResponse,
    summary="Analizar la coherencia entre las fuentes citadas",
)
def construir_coherencia(documento_id: str):
    """
    Construye el grafo de evidencias y conceptos, juzga las relaciones entre
    fragmentos de papers distintos (APOYA/CONTRADICE/COMPLEMENTA) y deriva los
    hallazgos. Requiere que la auditoría (EP-004) ya se haya ejecutado: los
    insumos son los fragmento_evidencia persistidos en las citas.

    Nota: `def` (no async) a propósito — FastAPI lo despacha a su threadpool
    y las llamadas LLM en paralelo no bloquean el event loop.
    """
    try:
        respuesta = coherencia_service.construir(documento_id)
    except Exception as e:
        logger.error("error_construir_coherencia", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "codigo": "ERROR_INTERNO",
                "mensaje": "El análisis de coherencia falló.",
                "accion_sugerida": "Intenta nuevamente.",
            },
        )

    if respuesta.total_evidencias == 0:
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "SIN_EVIDENCIAS",
                "mensaje": (
                    "No hay evidencias auditadas para analizar: ninguna cita "
                    "tiene veredicto SUPPORTS o REFUTES con fragmento de evidencia."
                ),
                "accion_sugerida": "Ejecuta primero la auditoría del documento.",
            },
        )
    return respuesta


@router.get(
    "/{documento_id}",
    response_model=CoherenciaResponse,
    summary="Ver el análisis de coherencia ya construido",
)
def ver_coherencia(documento_id: str):
    respuesta = coherencia_service.obtener(documento_id)
    if respuesta is None:
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "COHERENCIA_NO_CONSTRUIDA",
                "mensaje": "El análisis de coherencia aún no se ha ejecutado.",
                "accion_sugerida": "Ejecuta el análisis desde la fase de Cierre.",
            },
        )
    return respuesta


@router.get(
    "/{documento_id}/grafo",
    response_model=MapaCoherencia,
    summary="Mapa de referencias para la visualización",
)
def ver_grafo_coherencia(documento_id: str):
    respuesta = coherencia_service.obtener(documento_id)
    if respuesta is None:
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "COHERENCIA_NO_CONSTRUIDA",
                "mensaje": "El análisis de coherencia aún no se ha ejecutado.",
                "accion_sugerida": "Ejecuta el análisis desde la fase de Cierre.",
            },
        )
    return respuesta.mapa

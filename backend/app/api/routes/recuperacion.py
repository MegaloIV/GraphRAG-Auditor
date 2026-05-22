"""
EP-003: Endpoints del Motor de Recuperación Híbrida

HU-007  GET  /{doc_id}/motor/estado           → preparación del motor
HU-008  POST /{doc_id}/motor/consultar        → buscar cita por texto libre
HU-008  GET  /{doc_id}/motor/cita/{cita_id}   → consultar cita específica
HU-009  GET  /{doc_id}/motor/evidencia/{cita_id} → ruta de evidencia completa (grafo + fragmento)
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import structlog

from app.services.recuperacion.recuperacion_service import recuperacion_service, NodoGrafo

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/recuperacion", tags=["Recuperación"])


# ── Schemas de respuesta ─────────────────────────────────────────────────────

class NodoGrafoSchema(BaseModel):
    tipo: str
    nombre: str
    relacion: str
    propiedades: dict = Field(default_factory=dict)


class ResultadoRecuperacionSchema(BaseModel):
    cita_id: str
    texto_cita: str
    fragmento_relevante: str
    similitud: float
    nodos_grafo: list[NodoGrafoSchema]
    doi_referencia: str | None = None
    referencia_id: str | None = None
    metodo: str


class EstadoMotorSchema(BaseModel):
    listo: bool
    total_chunks: int
    total_citas_vinculadas: int
    total_referencias: int
    mensaje: str


class ConsultaLibreRequest(BaseModel):
    texto: str = Field(..., min_length=3, max_length=500,
                       description="Texto de la cita o término a buscar")
    n_resultados: int = Field(default=3, ge=1, le=10)


# ── Helper ────────────────────────────────────────────────────────────────────

def _nodo_to_schema(n: NodoGrafo) -> NodoGrafoSchema:
    return NodoGrafoSchema(
        tipo=n.tipo,
        nombre=n.nombre,
        relacion=n.relacion,
        propiedades=n.propiedades,
    )


# ── HU-007: Estado del motor ─────────────────────────────────────────────────

@router.get(
    "/{documento_id}/motor/estado",
    response_model=EstadoMotorSchema,
    summary="HU-007: Ver indicador de preparación del motor de búsqueda",
)
async def estado_motor(documento_id: str):
    """
    Indica si el motor de búsqueda semántica está listo para auditar
    el documento: verifica chunks indexados en ChromaDB y citas
    vinculadas a referencias en Neo4j.
    """
    try:
        estado = recuperacion_service.estado_motor(documento_id)
        return EstadoMotorSchema(
            listo=estado.listo,
            total_chunks=estado.total_chunks,
            total_citas_vinculadas=estado.total_citas_vinculadas,
            total_referencias=estado.total_referencias,
            mensaje=estado.mensaje,
        )
    except Exception as e:
        logger.error("error_estado_motor", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )


# ── HU-008: Consulta manual por texto libre ──────────────────────────────────

@router.post(
    "/{documento_id}/motor/consultar",
    response_model=list[ResultadoRecuperacionSchema],
    summary="HU-008: Consultar una cita específica manualmente (texto libre)",
)
async def consultar_texto_libre(documento_id: str, body: ConsultaLibreRequest):
    """
    Campo de búsqueda libre. Dado un texto, retorna los fragmentos
    del documento más relevantes junto con los nodos del grafo
    vinculados. Latencia objetivo: < 3 segundos.
    """
    try:
        resultados = recuperacion_service.consultar_texto_libre(
            documento_id=documento_id,
            texto_consulta=body.texto,
            n_resultados=body.n_resultados,
        )

        if not resultados:
            return []

        return [
            ResultadoRecuperacionSchema(
                cita_id=r.cita_id,
                texto_cita=r.texto_cita,
                fragmento_relevante=r.fragmento_relevante,
                similitud=r.similitud,
                nodos_grafo=[_nodo_to_schema(n) for n in r.nodos_grafo],
                doi_referencia=r.doi_referencia,
                referencia_id=r.referencia_id,
                metodo=r.metodo,
            )
            for r in resultados
        ]

    except Exception as e:
        logger.error("error_consulta_libre", doc_id=documento_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )


# ── HU-008 + HU-009: Consulta por cita_id específico ────────────────────────

@router.get(
    "/{documento_id}/motor/cita/{cita_id}",
    response_model=ResultadoRecuperacionSchema,
    summary="HU-008 / HU-009: Consultar cita específica con ruta de evidencia del grafo",
)
async def consultar_cita(documento_id: str, cita_id: str):
    """
    Recupera la evidencia completa para una cita específica:
    - Fragmento del paper más similar (ChromaDB)
    - Nodos del grafo vinculados: referencia, autores (Neo4j)
    - Score de similitud semántica

    Latencia objetivo: < 3 segundos (RN-005).
    """
    try:
        resultado = recuperacion_service.consultar_cita(
            documento_id=documento_id,
            cita_id=cita_id,
        )

        if resultado.metodo == "no_encontrado":
            raise HTTPException(
                status_code=404,
                detail={
                    "codigo": "CITA_NO_ENCONTRADA",
                    "mensaje": "No se encontró la cita o no tiene evidencia disponible.",
                    "accion_sugerida": "Verifica que la auditoría se haya completado.",
                },
            )

        return ResultadoRecuperacionSchema(
            cita_id=resultado.cita_id,
            texto_cita=resultado.texto_cita,
            fragmento_relevante=resultado.fragmento_relevante,
            similitud=resultado.similitud,
            nodos_grafo=[_nodo_to_schema(n) for n in resultado.nodos_grafo],
            doi_referencia=resultado.doi_referencia,
            referencia_id=resultado.referencia_id,
            metodo=resultado.metodo,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("error_consulta_cita", doc_id=documento_id, cita_id=cita_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail={"codigo": "ERROR_INTERNO", "mensaje": str(e),
                    "accion_sugerida": "Intenta nuevamente."},
        )


# ── HU-009: Ruta de evidencia explícita ─────────────────────────────────────

@router.get(
    "/{documento_id}/motor/evidencia/{cita_id}",
    response_model=ResultadoRecuperacionSchema,
    summary="HU-009: Ver la ruta de evidencia en el grafo para una cita",
)
async def ver_evidencia_grafo(documento_id: str, cita_id: str):
    """
    Alias semántico de /motor/cita/{cita_id} orientado a mostrar
    explícitamente la ruta grafo (nodos + relaciones) junto al fragmento.
    Cumple HU-009: texto + grafo en < 3 segundos.
    """
    return await consultar_cita(documento_id, cita_id)
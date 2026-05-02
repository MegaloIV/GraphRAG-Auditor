from pathlib import Path
import structlog
from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.models.grafo import ReferenciasResponse, CitasResponse, ResumenGrafo
from app.services.ingesta.pdf_service import pdf_service, PDFNoProcessableError
from app.services.grafo.extraccion_service import extraccion_service
from app.services.grafo.grafo_carga_service import grafo_carga_service

logger = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/grafo", tags=["Grafo"])


@router.get(
    "/{documento_id}/referencias",
    response_model=ReferenciasResponse,
    summary="HU-004: Ver referencias bibliográficas identificadas",
)
async def ver_referencias(documento_id: str):
    """
    Retorna las referencias APA extraídas del documento.
    """
    ruta_pdf = Path(settings.upload_dir) / f"{documento_id}.pdf"

    if not ruta_pdf.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "DOCUMENTO_NO_ENCONTRADO",
                "mensaje": "No se encontró el documento.",
                "accion_sugerida": "Vuelve a cargar el archivo PDF.",
            },
        )

    try:
        texto_md, _ = pdf_service.extraer_texto(ruta_pdf)
    except PDFNoProcessableError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": e.codigo,
                "mensaje": e.mensaje,
                "accion_sugerida": e.accion,
            },
        )

    referencias = extraccion_service.extraer_referencias(texto_md)
    incompletas = sum(1 for r in referencias if r.datos_incompletos)

    advertencia = None
    if incompletas > 0:
        advertencia = (
            f"Se detectaron {incompletas} referencias con datos incompletos."
        )

    return ReferenciasResponse(
        documento_id=documento_id,
        total_referencias=len(referencias),
        referencias=referencias,
        referencias_incompletas=incompletas,
        advertencia=advertencia,
    )


@router.get(
    "/{documento_id}/citas",
    response_model=CitasResponse,
    summary="HU-005: Ver citas detectadas en el cuerpo del texto",
)
async def ver_citas(documento_id: str):
    """
    Detecta y retorna todas las citas APA en el texto.
    """
    ruta_pdf = Path(settings.upload_dir) / f"{documento_id}.pdf"

    if not ruta_pdf.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "DOCUMENTO_NO_ENCONTRADO",
                "mensaje": "No se encontró el documento.",
                "accion_sugerida": "Vuelve a cargar el archivo PDF.",
            },
        )

    try:
        texto_md, num_paginas = pdf_service.extraer_texto(ruta_pdf)
    except PDFNoProcessableError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": e.codigo,
                "mensaje": e.mensaje,
                "accion_sugerida": e.accion,
            },
        )

    citas = extraccion_service.extraer_citas(texto_md, num_paginas)
    parenteticas = sum(1 for c in citas if c.tipo.value == "parentetica")
    narrativas = sum(1 for c in citas if c.tipo.value == "narrativa")

    advertencia = None
    if not citas:
        advertencia = (
            "No se detectaron citas APA en el documento. "
            "Verifica que el documento use formato APA 7ma edición."
        )

    return CitasResponse(
        documento_id=documento_id,
        total_citas=len(citas),
        citas_parenteticas=parenteticas,
        citas_narrativas=narrativas,
        citas=citas,
        advertencia=advertencia,
    )


@router.get(
    "/{documento_id}/resumen",
    response_model=ResumenGrafo,
    summary="HU-006: Ver confirmación del grafo construido",
)
async def ver_resumen_grafo(documento_id: str):
    """
    Retorna el resumen del grafo de conocimiento construido.
    """
    try:
        return grafo_carga_service.obtener_resumen_grafo(documento_id)
    except Exception as e:
        logger.error("error_resumen_grafo", doc_id=documento_id, error=str(e))
        return ResumenGrafo(
            documento_id=documento_id,
            total_nodos=0,
            nodos_autores=0,
            nodos_referencias=0,
            nodos_citas=0,
            total_relaciones=0,
            densidad_promedio=0.0,
            grafo_robusto=False,
            error="No se pudo obtener el resumen del grafo. Verifica que la auditoría se haya completado.",
        )
import uuid
from pathlib import Path
import structlog
from fastapi import APIRouter, File, UploadFile, HTTPException, BackgroundTasks

from app.core.config import get_settings
from app.models.ingesta import (
    DocumentoCargadoResponse,
    EstructuraDocumentoResponse,
    ProgresoAuditoriaResponse,
    EstadoIngesta,
)
from app.services.ingesta.pdf_service import pdf_service, PDFNoProcessableError

logger = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/ingesta", tags=["Ingesta"])

# Diccionario en memoria para guardar el progreso de cada auditoría
# En producción esto sería Redis, pero para el proyecto académico es suficiente
_progreso: dict[str, ProgresoAuditoriaResponse] = {}


@router.post(
    "/cargar",
    response_model=DocumentoCargadoResponse,
    summary="HU-001: Cargar documento PDF",
)
async def cargar_pdf(archivo: UploadFile = File(...)):
    """
    Recibe un PDF, lo valida y lo guarda para su procesamiento.
    """
    contenido = await archivo.read()

    try:
        pdf_service.validar_pdf(contenido, archivo.filename or "")
    except PDFNoProcessableError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": e.codigo,
                "mensaje": e.mensaje,
                "accion_sugerida": e.accion,
            },
        )

    documento_id = str(uuid.uuid4())
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ruta_pdf = upload_dir / f"{documento_id}.pdf"
    ruta_pdf.write_bytes(contenido)

    try:
        _, num_paginas = pdf_service.extraer_texto(ruta_pdf)
    except PDFNoProcessableError as e:
        ruta_pdf.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": e.codigo,
                "mensaje": e.mensaje,
                "accion_sugerida": e.accion,
            },
        )

    logger.info("pdf_cargado", doc_id=documento_id, paginas=num_paginas)

    return DocumentoCargadoResponse(
        documento_id=documento_id,
        nombre_archivo=archivo.filename or "documento.pdf",
        tamano_bytes=len(contenido),
        paginas=num_paginas,
        estado=EstadoIngesta.COMPLETADO,
        mensaje="Documento recibido correctamente.",
    )


@router.get(
    "/{documento_id}/estructura",
    response_model=EstructuraDocumentoResponse,
    summary="HU-002: Ver estructura detectada del documento",
)
async def ver_estructura(documento_id: str):
    """
    Analiza y retorna las secciones detectadas en el documento.
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
        return pdf_service.detectar_secciones(texto_md, num_paginas, documento_id)
    except PDFNoProcessableError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": e.codigo,
                "mensaje": e.mensaje,
                "accion_sugerida": e.accion,
            },
        )


@router.post(
    "/{documento_id}/iniciar-auditoria",
    response_model=ProgresoAuditoriaResponse,
    summary="HU-003: Iniciar proceso de auditoría",
)
async def iniciar_auditoria(documento_id: str, background_tasks: BackgroundTasks):
    """
    Inicia el pipeline completo de auditoría en background.
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

    _progreso[documento_id] = ProgresoAuditoriaResponse(
        documento_id=documento_id,
        estado=EstadoIngesta.PROCESANDO,
        porcentaje=0,
        mensaje_progreso="Iniciando auditoría...",
    )

    background_tasks.add_task(_ejecutar_pipeline, documento_id, ruta_pdf)

    return _progreso[documento_id]


@router.get(
    "/{documento_id}/progreso",
    response_model=ProgresoAuditoriaResponse,
    summary="HU-003: Consultar progreso de auditoría",
)
async def ver_progreso(documento_id: str):
    """
    El frontend consulta este endpoint cada pocos segundos
    para mostrar el progreso en tiempo real.
    """
    if documento_id not in _progreso:
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "AUDITORIA_NO_INICIADA",
                "mensaje": "No hay auditoría activa para este documento.",
                "accion_sugerida": "Inicia la auditoría primero.",
            },
        )
    return _progreso[documento_id]


async def _ejecutar_pipeline(documento_id: str, ruta_pdf: Path) -> None:
    """
    Pipeline completo ejecutado en background.
    Actualiza _progreso en cada etapa para que el frontend lo consulte.
    """
    from app.services.grafo.extraccion_service import extraccion_service
    from app.services.grafo.grafo_carga_service import grafo_carga_service

    def actualizar(porcentaje: int, mensaje: str) -> None:
        _progreso[documento_id] = ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.PROCESANDO,
            porcentaje=porcentaje,
            mensaje_progreso=mensaje,
        )

    try:
        actualizar(10, "Extrayendo texto del PDF...")
        texto_md, num_paginas = pdf_service.extraer_texto(ruta_pdf)

        actualizar(25, "Analizando estructura del documento...")
        pdf_service.detectar_secciones(texto_md, num_paginas, documento_id)

        actualizar(40, "Extrayendo referencias bibliográficas...")
        referencias = extraccion_service.extraer_referencias(texto_md)

        actualizar(60, "Detectando citas en el texto...")
        citas = extraccion_service.extraer_citas(texto_md, num_paginas)

        actualizar(75, "Construyendo grafo de conocimiento...")
        grafo_carga_service.cargar_documento(documento_id, ruta_pdf.name)
        grafo_carga_service.cargar_referencias(documento_id, referencias)
        grafo_carga_service.cargar_citas(documento_id, citas)

        _progreso[documento_id] = ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.COMPLETADO,
            porcentaje=100,
            mensaje_progreso="Auditoría completada exitosamente.",
            citas_encontradas=len(citas),
        )
        logger.info("auditoria_completada", doc_id=documento_id, citas=len(citas))

    except Exception as e:
        logger.error("auditoria_fallida", doc_id=documento_id, error=str(e))
        _progreso[documento_id] = ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.ERROR,
            porcentaje=0,
            mensaje_progreso="La auditoría falló.",
            error="Ocurrió un error durante el procesamiento. Intenta nuevamente.",
        )
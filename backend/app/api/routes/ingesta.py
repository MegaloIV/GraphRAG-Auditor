import json
import uuid
import threading
from pathlib import Path
import structlog
import asyncio
from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import StreamingResponse

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

# ── Progreso en disco (sobrevive reinicios) ───────────────
PROGRESO_DIR = Path("./data/progreso")


def _guardar_progreso(progreso: ProgresoAuditoriaResponse) -> None:
    PROGRESO_DIR.mkdir(parents=True, exist_ok=True)
    ruta = PROGRESO_DIR / f"{progreso.documento_id}.json"
    ruta.write_text(progreso.model_dump_json(), encoding="utf-8")


def _leer_progreso(documento_id: str) -> ProgresoAuditoriaResponse | None:
    ruta = PROGRESO_DIR / f"{documento_id}.json"
    if not ruta.exists():
        return None
    try:
        contenido = ruta.read_text(encoding="utf-8").strip()
        if not contenido:
            return None
        datos = json.loads(contenido)
        return ProgresoAuditoriaResponse(**datos)
    except (json.JSONDecodeError, Exception):
        return None


@router.post(
    "/cargar",
    response_model=DocumentoCargadoResponse,
    summary="HU-001: Cargar documento PDF",
)
async def cargar_pdf(archivo: UploadFile = File(...)):
    """
    Recibe un PDF, lo valida, guarda y arranca la auditoría automáticamente.
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

    # Registrar progreso inicial en disco
    _guardar_progreso(ProgresoAuditoriaResponse(
        documento_id=documento_id,
        estado=EstadoIngesta.PROCESANDO,
        porcentaje=0,
        mensaje_progreso="Iniciando auditoría...",
    ))

    # Arrancar pipeline en thread del proceso principal
    hilo = threading.Thread(
        target=_ejecutar_pipeline,
        args=(documento_id, ruta_pdf),
        daemon=True,
    )
    hilo.start()

    return DocumentoCargadoResponse(
        documento_id=documento_id,
        nombre_archivo=archivo.filename or "documento.pdf",
        tamano_bytes=len(contenido),
        paginas=num_paginas,
        estado=EstadoIngesta.PROCESANDO,
        mensaje="Documento recibido. Auditoría iniciada automáticamente.",
    )


@router.get(
    "/{documento_id}/estructura",
    response_model=EstructuraDocumentoResponse,
    summary="HU-002: Ver estructura detectada del documento",
)
async def ver_estructura(documento_id: str):
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


@router.get(
    "/{documento_id}/progreso",
    summary="HU-003: Stream de progreso en tiempo real (SSE)",
)
async def ver_progreso(documento_id: str):
    logger.info("sse_conectado", doc_id=documento_id)

    async def generador():
        ultimo_porcentaje = -1
        intentos_sin_cambio = 0
        ticks = 0

        while True:
            progreso = _leer_progreso(documento_id)

            if not progreso:
                await asyncio.sleep(0.5)
                intentos_sin_cambio += 1
                if intentos_sin_cambio > 20:
                    payload = json.dumps({
                        "documento_id": documento_id,
                        "estado": "error",
                        "porcentaje": 0,
                        "mensaje_progreso": "No se encontró la auditoría.",
                        "error": "Documento no encontrado.",
                    })
                    yield f"data: {payload}\n\n"
                    return
                yield ": keep-alive\n\n"
                continue

            if progreso.porcentaje != ultimo_porcentaje:
                ultimo_porcentaje = progreso.porcentaje
                ticks = 0
                yield f"data: {progreso.model_dump_json()}\n\n"

            if progreso.estado in (EstadoIngesta.COMPLETADO, EstadoIngesta.ERROR):
                logger.info("sse_finalizado", doc_id=documento_id, estado=progreso.estado)
                return

            await asyncio.sleep(0.5)
            ticks += 1
            if ticks % 30 == 0:
                yield ": keep-alive\n\n"

    return StreamingResponse(
        generador(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _ejecutar_pipeline(documento_id: str, ruta_pdf: Path) -> None:
    import logging
    from app.services.grafo.extraccion_service import extraccion_service
    from app.services.grafo.grafo_carga_service import grafo_carga_service

    _log = logging.getLogger(__name__)

    def actualizar(porcentaje: int, mensaje: str) -> None:
        _log.info(f"[pipeline] {porcentaje}% — {mensaje}")
        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.PROCESANDO,
            porcentaje=porcentaje,
            mensaje_progreso=mensaje,
        ))

    try:
        actualizar(10, "Extrayendo texto del PDF...")
        texto_md, num_paginas = pdf_service.extraer_texto(ruta_pdf)

        actualizar(25, "Analizando estructura del documento...")
        pdf_service.detectar_secciones(texto_md, num_paginas, documento_id)

        actualizar(40, "Extrayendo referencias bibliográficas...")
        referencias = extraccion_service.extraer_referencias(texto_md)

        actualizar(55, "Detectando citas en el texto...")
        citas = extraccion_service.extraer_citas(texto_md, num_paginas)

        actualizar(70, "Construyendo grafo de conocimiento...")
        grafo_carga_service.cargar_documento(documento_id, ruta_pdf.name)
        grafo_carga_service.cargar_referencias(documento_id, referencias)
        grafo_carga_service.cargar_citas(documento_id, citas)

        actualizar(78, "Vinculando citas con sus referencias...")
        resultado_vinculacion = grafo_carga_service.vincular_citas(documento_id)
        _log.info(f"[pipeline] vinculacion_completada {resultado_vinculacion}")

        actualizar(85, "Verificando referencias en CrossRef...")
        from app.services.externo.verificacion_service import verificacion_service
        verificacion_service.verificar_referencias(referencias, documento_id)

        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.COMPLETADO,
            porcentaje=100,
            mensaje_progreso="Auditoría completada exitosamente.",
            citas_encontradas=len(citas),
        ))
        _log.info(f"[pipeline] auditoria_completada doc_id={documento_id} citas={len(citas)}")

    except Exception as e:
        _log.error(f"[pipeline] auditoria_fallida doc_id={documento_id} error={str(e)}", exc_info=True)
        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.ERROR,
            porcentaje=0,
            mensaje_progreso="La auditoría falló.",
            error="Ocurrió un error durante el procesamiento. Intenta nuevamente.",
        ))
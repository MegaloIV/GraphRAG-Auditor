import json
import uuid
import threading
from pathlib import Path
import structlog
import asyncio
from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse, FileResponse

from app.core.config import get_settings
from app.models.ingesta import (
    DocumentoCargadoResponse,
    EstructuraDocumentoResponse,
    ProgresoAuditoriaResponse,
    EstadoIngesta,
    VerificacionSolicitud,
)
from app.services.ingesta.pdf_service import pdf_service, PDFNoProcessableError
from app.services.ingesta.progreso_repository import progreso_repository
from app.services.storage.supabase_storage_service import storage_service

logger = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/ingesta", tags=["Ingesta"])


def _objeto_pdf(documento_id: str) -> str:
    return f"uploads/{documento_id}.pdf"


def _objeto_markdown(documento_id: str) -> str:
    return f"processed/{documento_id}.md"


def _guardar_progreso(progreso: ProgresoAuditoriaResponse) -> None:
    progreso_repository.guardar(progreso)


def _leer_progreso(documento_id: str) -> ProgresoAuditoriaResponse | None:
    return progreso_repository.leer(documento_id)


@router.post(
    "/cargar",
    response_model=DocumentoCargadoResponse,
    summary="HU-001: Cargar documento PDF",
)
async def cargar_pdf(archivo: UploadFile = File(...)):
    """
    Recibe un PDF, lo valida, guarda y arranca la fase 1 del pipeline automáticamente.
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

    # Guardar el PDF en la nube (Supabase Storage) y obtener una ruta local
    # temporal para el procesamiento con PyMuPDF.
    storage_service.subir_bytes(
        _objeto_pdf(documento_id), contenido, content_type="application/pdf"
    )
    ruta_pdf = storage_service.obtener_local(_objeto_pdf(documento_id))

    try:
        _, num_paginas = pdf_service.extraer_texto(ruta_pdf)
    except PDFNoProcessableError as e:
        storage_service.eliminar(_objeto_pdf(documento_id))
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": e.codigo,
                "mensaje": e.mensaje,
                "accion_sugerida": e.accion,
            },
        )

    logger.info("pdf_cargado", doc_id=documento_id, paginas=num_paginas)

    _guardar_progreso(ProgresoAuditoriaResponse(
        documento_id=documento_id,
        estado=EstadoIngesta.PROCESANDO,
        porcentaje=0,
        mensaje_progreso="Iniciando extracción...",
    ))

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
        mensaje="Documento recibido. Extracción iniciada.",
    )


@router.post(
    "/{documento_id}/verificar",
    summary="HU-VER: Iniciar verificación externa de referencias seleccionadas",
)
async def iniciar_verificacion(documento_id: str, solicitud: VerificacionSolicitud):
    """
    Recibe los IDs de las referencias seleccionadas por el usuario y arranca
    la verificación externa (CrossRef + Unpaywall) directamente sobre ellas.
    Las citas obtienen cobertura de paper a través de su referencia vinculada.
    """
    progreso = _leer_progreso(documento_id)
    estados_validos = {EstadoIngesta.LISTO_EXTRACCION, EstadoIngesta.COMPLETADO}
    if not progreso or progreso.estado not in estados_validos:
        raise HTTPException(
            status_code=400,
            detail={
                "codigo": "ESTADO_INVALIDO",
                "mensaje": "El documento no está listo para verificación.",
                "accion_sugerida": "Espera a que la extracción termine antes de verificar.",
            },
        )

    if not solicitud.referencia_ids:
        raise HTTPException(
            status_code=400,
            detail={
                "codigo": "SIN_REFERENCIAS",
                "mensaje": "Debes seleccionar al menos una referencia para verificar.",
                "accion_sugerida": "Selecciona una o más referencias en la lista.",
            },
        )

    # Mark as VERIFICANDO before launching the thread so the SSE stream,
    # which connects right after this response, sees a non-terminal state.
    _guardar_progreso(ProgresoAuditoriaResponse(
        documento_id=documento_id,
        estado=EstadoIngesta.VERIFICANDO,
        porcentaje=0,
        mensaje_progreso="Iniciando verificación...",
    ))

    hilo = threading.Thread(
        target=_ejecutar_verificacion,
        args=(documento_id, solicitud.referencia_ids),
        daemon=True,
    )
    hilo.start()

    return {"mensaje": "Verificación iniciada.", "documento_id": documento_id}


@router.post(
    "/{documento_id}/importar-zotero",
    summary="Importar colección de Zotero (.ris o .zip con PDFs) y asociar papers",
)
async def importar_zotero(documento_id: str, archivo: UploadFile = File(...)):
    """
    Empareja las entradas del RIS con las referencias del documento (DOI exacto,
    o título+autor+año), sobrescribe metadatos con los de Zotero y asocia los
    PDFs (adjuntos del ZIP o descarga open access por DOI). Corre en segundo
    plano; el avance se sigue por el SSE de progreso y el resumen queda en
    GET /{id}/importar-zotero/resultado.
    """
    progreso = _leer_progreso(documento_id)
    estados_validos = {EstadoIngesta.LISTO_EXTRACCION, EstadoIngesta.COMPLETADO}
    if not progreso or progreso.estado not in estados_validos:
        raise HTTPException(
            status_code=400,
            detail={
                "codigo": "ESTADO_INVALIDO",
                "mensaje": "El documento no está listo para importar la colección.",
                "accion_sugerida": "Confirma la revisión antes de importar desde Zotero.",
            },
        )

    nombre = archivo.filename or ""
    if not nombre.lower().endswith((".ris", ".zip")):
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": "FORMATO_INVALIDO",
                "mensaje": "El archivo debe ser un .ris de Zotero o un .zip exportado con archivos.",
                "accion_sugerida": "En Zotero: clic derecho en la colección → Exportar (formato RIS).",
            },
        )

    contenido = await archivo.read()

    _guardar_progreso(ProgresoAuditoriaResponse(
        documento_id=documento_id,
        estado=EstadoIngesta.VERIFICANDO,
        porcentaje=0,
        mensaje_progreso="Importando colección de Zotero...",
    ))

    hilo = threading.Thread(
        target=_ejecutar_importacion_zotero,
        args=(documento_id, contenido, nombre, progreso.estado),
        daemon=True,
    )
    hilo.start()

    return {"mensaje": "Importación iniciada.", "documento_id": documento_id}


@router.get(
    "/{documento_id}/importar-zotero/resultado",
    summary="Resumen de la última importación de Zotero",
)
async def resultado_zotero(documento_id: str):
    from app.services.externo.zotero_service import zotero_service

    resumen = zotero_service.leer_resultado(documento_id)
    if resumen is None:
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "IMPORTACION_NO_ENCONTRADA",
                "mensaje": "No hay una importación de Zotero para este documento.",
                "accion_sugerida": "Importa primero un archivo .ris o .zip.",
            },
        )
    return resumen


@router.get(
    "/{documento_id}/markdown",
    summary="Texto Markdown del documento procesado (para la fase de revisión)",
)
async def ver_markdown(documento_id: str):
    """
    Devuelve el Markdown extraído del PDF en texto plano. Se usa en el visor
    de la fase de revisión humana para resaltar las citas detectadas.
    """
    texto_md = storage_service.leer_texto(_objeto_markdown(documento_id))
    if texto_md is None:
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "MARKDOWN_NO_ENCONTRADO",
                "mensaje": "No se encontró el texto procesado del documento.",
                "accion_sugerida": "Vuelve a cargar el archivo PDF.",
            },
        )
    return PlainTextResponse(content=texto_md, media_type="text/plain; charset=utf-8")


@router.get(
    "/{documento_id}/pdf",
    summary="PDF original del documento (para el visor de la fase de revisión)",
)
async def ver_pdf(documento_id: str):
    """
    Sirve el PDF tal cual fue subido. El visor de la revisión humana lo
    renderiza y superpone los resaltados de GET /grafo/{id}/citas/ubicaciones.
    """
    ruta_pdf = storage_service.obtener_local(_objeto_pdf(documento_id))
    if ruta_pdf is None:
        raise HTTPException(
            status_code=404,
            detail={
                "codigo": "DOCUMENTO_NO_ENCONTRADO",
                "mensaje": "No se encontró el PDF del documento.",
                "accion_sugerida": "Vuelve a cargar el archivo PDF.",
            },
        )
    return FileResponse(
        ruta_pdf,
        media_type="application/pdf",
        filename=f"{documento_id}.pdf",
        content_disposition_type="inline",
    )


@router.post(
    "/{documento_id}/confirmar-revision",
    summary="Confirmar la revisión humana de citas y referencias",
)
async def confirmar_revision(documento_id: str):
    """
    Cierra la fase de revisión: pasa de REVISION_PENDIENTE a LISTO_EXTRACCION,
    desbloqueando el flujo de verificación externa.
    """
    progreso = _leer_progreso(documento_id)
    if not progreso or progreso.estado != EstadoIngesta.REVISION_PENDIENTE:
        raise HTTPException(
            status_code=400,
            detail={
                "codigo": "ESTADO_INVALIDO",
                "mensaje": "El documento no está en fase de revisión.",
                "accion_sugerida": "Espera a que la extracción termine antes de confirmar.",
            },
        )

    _guardar_progreso(ProgresoAuditoriaResponse(
        documento_id=documento_id,
        estado=EstadoIngesta.LISTO_EXTRACCION,
        porcentaje=100,
        mensaje_progreso="Revisión confirmada. Selecciona las referencias a verificar.",
        citas_encontradas=progreso.citas_encontradas,
    ))
    logger.info("revision_confirmada", doc_id=documento_id)
    return {"documento_id": documento_id, "estado": EstadoIngesta.LISTO_EXTRACCION.value}


@router.get(
    "/{documento_id}/estructura",
    response_model=EstructuraDocumentoResponse,
    summary="HU-002: Ver estructura detectada del documento",
)
async def ver_estructura(documento_id: str):
    ruta_pdf = storage_service.obtener_local(_objeto_pdf(documento_id))

    if ruta_pdf is None:
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

            estados_terminales = (
                EstadoIngesta.COMPLETADO,
                EstadoIngesta.ERROR,
                EstadoIngesta.LISTO_EXTRACCION,
                EstadoIngesta.REVISION_PENDIENTE,
            )
            if progreso.estado in estados_terminales:
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


# ── Pipeline fase 1: extracción + grafo ──────────────────────────────────────

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

        # Persistir el Markdown para que la fase de revisión humana pueda
        # mostrarlo en el visor del frontend (GET /ingesta/{id}/markdown).
        storage_service.subir_texto(_objeto_markdown(documento_id), texto_md)

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

        actualizar(90, "Vinculando citas con sus referencias...")
        resultado_vinculacion = grafo_carga_service.vincular_citas(documento_id)
        _log.info(f"[pipeline] vinculacion_completada {resultado_vinculacion}")

        # Reemplazar la página estimada por bloques con la página real del PDF
        # (PyMuPDF search_for) y dejar listo el cache de ubicaciones que usa
        # el visor de la revisión.
        actualizar(95, "Localizando citas en el PDF...")
        try:
            from app.services.ingesta.localizacion_service import localizacion_service
            localizacion_service.sincronizar_paginas(documento_id)
        except Exception as e:
            # La localización es mejor-esfuerzo: si falla, las cartillas
            # conservan la página estimada y la revisión sigue funcionando.
            _log.warning(f"[pipeline] localizacion_fallida doc_id={documento_id} error={e}")

        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.REVISION_PENDIENTE,
            porcentaje=100,
            mensaje_progreso="Extracción completada. Revisa y confirma las citas y referencias antes de continuar.",
            citas_encontradas=len(citas),
        ))
        _log.info(f"[pipeline] extraccion_completada doc_id={documento_id} citas={len(citas)}")

    except Exception as e:
        _log.error(f"[pipeline] extraccion_fallida doc_id={documento_id} error={str(e)}", exc_info=True)
        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.ERROR,
            porcentaje=0,
            mensaje_progreso="La extracción falló.",
            error="Ocurrió un error durante el procesamiento. Intenta nuevamente.",
        ))


# ── Importación de colección Zotero (segundo plano) ─────────────────────────

def _ejecutar_importacion_zotero(
    documento_id: str, contenido: bytes, nombre: str, estado_previo: EstadoIngesta
) -> None:
    import logging
    from app.services.externo.zotero_service import zotero_service

    _log = logging.getLogger(__name__)

    def notificar(porcentaje: int, mensaje: str) -> None:
        _log.info(f"[zotero] {porcentaje}% — {mensaje}")
        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.VERIFICANDO,
            porcentaje=porcentaje,
            mensaje_progreso=mensaje,
        ))

    try:
        resumen = zotero_service.importar(documento_id, contenido, nombre, notificar)
        emparejadas = resumen["emparejadas_por_doi"] + resumen["emparejadas_por_titulo"]
        con_pdf = resumen["con_pdf_zotero"] + resumen["descargadas_unpaywall"]
        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.COMPLETADO,
            porcentaje=100,
            mensaje_progreso=(
                f"Importación completada: {emparejadas} referencias emparejadas, "
                f"{con_pdf} con paper indexado."
            ),
        ))
    except Exception as e:
        _log.error(f"[zotero] importacion_fallida doc_id={documento_id} error={e}", exc_info=True)
        # Restaurar el estado previo: un fallo de importación no debe
        # bloquear el documento (el riel de fases depende del estado).
        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=estado_previo,
            porcentaje=100,
            mensaje_progreso="La importación de Zotero falló.",
            error=str(e) if isinstance(e, ValueError) else "Ocurrió un error durante la importación.",
        ))


# ── Pipeline fase 2: verificación externa ────────────────────────────────────

def _ejecutar_verificacion(documento_id: str, referencia_ids: list[str]) -> None:
    import logging
    from app.services.grafo.neo4j_service import neo4j_service
    from app.services.externo.verificacion_service import verificacion_service
    from app.models.grafo import ReferenciaAPA

    _log = logging.getLogger(__name__)

    def actualizar(porcentaje: int, mensaje: str) -> None:
        _log.info(f"[verificacion] {porcentaje}% — {mensaje}")
        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.VERIFICANDO,
            porcentaje=porcentaje,
            mensaje_progreso=mensaje,
        ))

    try:
        actualizar(10, "Cargando referencias seleccionadas...")

        # Consulta directa sobre las referencias por ID — sin JOIN por citas.
        # Las citas obtienen cobertura de paper a través de su Referencia vinculada.
        query = """
        MATCH (r:Referencia)
        WHERE r.id IN $referencia_ids
        OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
        RETURN r, collect(a.nombre) AS autores
        """
        referencias_seleccionadas: list[ReferenciaAPA] = []
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            result = session.run(query, referencia_ids=referencia_ids)
            for record in result:
                r = record["r"]
                referencias_seleccionadas.append(ReferenciaAPA(
                    referencia_id=r["id"],
                    autores=record["autores"],
                    anio=r.get("anio"),
                    titulo=r.get("titulo", ""),
                    fuente=r.get("fuente"),
                    doi=r.get("doi"),
                    datos_incompletos=r.get("datos_incompletos", False),
                    campos_faltantes=[],
                ))

        total = len(referencias_seleccionadas)
        _log.info(f"[verificacion] referencias={total} doc_id={documento_id}")

        if total == 0:
            _guardar_progreso(ProgresoAuditoriaResponse(
                documento_id=documento_id,
                estado=EstadoIngesta.COMPLETADO,
                porcentaje=100,
                mensaje_progreso="No se encontraron las referencias indicadas. Pipeline completado.",
            ))
            return

        actualizar(30, f"Verificando {total} referencia(s) en CrossRef y Unpaywall...")
        verificacion_service.verificar_referencias(
            referencias_seleccionadas,
            documento_id,
            max_referencias=total,
        )

        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.COMPLETADO,
            porcentaje=100,
            mensaje_progreso="Verificación completada. Listo para auditar.",
        ))
        _log.info(f"[verificacion] completada doc_id={documento_id} refs={total}")

    except Exception as e:
        _log.error(f"[verificacion] fallida doc_id={documento_id} error={str(e)}", exc_info=True)
        _guardar_progreso(ProgresoAuditoriaResponse(
            documento_id=documento_id,
            estado=EstadoIngesta.ERROR,
            porcentaje=0,
            mensaje_progreso="La verificación falló.",
            error="Ocurrió un error durante la verificación. Intenta nuevamente.",
        ))

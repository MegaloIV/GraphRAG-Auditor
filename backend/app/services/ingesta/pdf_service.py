import re
import time
from pathlib import Path
from typing import Optional
import structlog

from app.core.config import get_settings
from app.models.ingesta import (
    EstructuraDocumentoResponse,
    SeccionDetectada,
    TipoSeccion,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

# Patrones para detectar secciones por su título
PATRONES_SECCIONES: dict[TipoSeccion, list[str]] = {
    TipoSeccion.REFERENCIAS: [
        r"^referencias?\s*$",
        r"^bibliograf[íi]a\s*$",
        r"^references?\s*$",
        r"^works?\s+cited\s*$",
    ],
    TipoSeccion.INTRODUCCION: [
        r"^introducci[oó]n\s*$",
        r"^introduction\s*$",
    ],
    TipoSeccion.METODOLOGIA: [
        r"^metodolog[íi]a\s*$",
        r"^m[eé]todos?\s*$",
        r"^methodology\s*$",
    ],
    TipoSeccion.RESULTADOS: [
        r"^resultados?\s*$",
        r"^results?\s*$",
    ],
    TipoSeccion.DISCUSION: [
        r"^discusi[oó]n\s*$",
        r"^discussion\s*$",
    ],
    TipoSeccion.CONCLUSION: [
        r"^conclusi[oó]n(es)?\s*$",
        r"^conclusion(s)?\s*$",
    ],
    TipoSeccion.RESUMEN: [
        r"^resumen\s*$",
        r"^abstract\s*$",
    ],
}


class PDFNoProcessableError(Exception):
    """Se lanza cuando el PDF no puede ser procesado."""
    def __init__(self, codigo: str, mensaje: str, accion: str):
        self.codigo = codigo
        self.mensaje = mensaje
        self.accion = accion
        super().__init__(mensaje)


class PDFExtractionService:

    def validar_pdf(self, contenido: bytes, nombre_archivo: str) -> None:
        """
        Valida el archivo antes de procesarlo.
        HU-001 / RN-002: errores claros sin detalles técnicos internos.
        """
        if not nombre_archivo.lower().endswith(".pdf"):
            raise PDFNoProcessableError(
                codigo="FORMATO_INVALIDO",
                mensaje="El archivo no es un PDF válido.",
                accion="Sube un archivo con extensión .pdf",
            )

        if len(contenido) > settings.max_pdf_size_bytes:
            tamano_mb = len(contenido) / (1024 * 1024)
            raise PDFNoProcessableError(
                codigo="ARCHIVO_DEMASIADO_GRANDE",
                mensaje=f"El archivo pesa {tamano_mb:.1f} MB y supera el límite de {settings.max_pdf_size_mb} MB.",
                accion="Comprime el PDF o divídelo en partes más pequeñas.",
            )

        if not contenido.startswith(b"%PDF"):
            raise PDFNoProcessableError(
                codigo="PDF_CORRUPTO",
                mensaje="El archivo no tiene una estructura PDF válida.",
                accion="Verifica que el archivo no esté dañado e intenta exportarlo nuevamente.",
            )

    def extraer_texto(self, ruta_pdf: Path) -> tuple[str, int]:
        """
        Extrae texto del PDF como Markdown usando pymupdf4llm.
        RN-001: debe completarse en menos de 30s para 50 páginas.
        Retorna (texto_markdown, num_paginas).
        """
        try:
            import pymupdf4llm
            import fitz

            inicio = time.perf_counter()

            doc = fitz.open(str(ruta_pdf))
            num_paginas = len(doc)
            doc.close()

            if num_paginas == 0:
                raise PDFNoProcessableError(
                    codigo="PDF_SIN_CONTENIDO",
                    mensaje="El PDF no contiene páginas con texto.",
                    accion="Verifica que el documento no esté vacío.",
                )

            texto_md = pymupdf4llm.to_markdown(str(ruta_pdf))

            if len(texto_md.strip()) < 100:
                raise PDFNoProcessableError(
                    codigo="PDF_ESCANEADO_SIN_TEXTO",
                    mensaje="El PDF parece ser un documento escaneado sin texto digital.",
                    accion="Usa una herramienta de OCR para generar una versión con texto seleccionable.",
                )

            elapsed = time.perf_counter() - inicio
            logger.info("pdf_extraido", paginas=num_paginas, tiempo_segundos=round(elapsed, 2))

            if elapsed > 30:
                logger.warning("extraccion_lenta", tiempo_segundos=round(elapsed, 2))

            return texto_md, num_paginas

        except PDFNoProcessableError:
            raise
        except Exception:
            raise PDFNoProcessableError(
                codigo="ERROR_LECTURA_PDF",
                mensaje="No se pudo leer el contenido del PDF.",
                accion="Verifica que el archivo no esté protegido con contraseña.",
            )

    def detectar_secciones(self, texto_md: str, num_paginas: int, documento_id: str) -> EstructuraDocumentoResponse:
        """
        Analiza el Markdown y detecta las secciones del documento.
        HU-002: advierte si no encuentra sección de referencias.
        """
        lineas = texto_md.split("\n")
        secciones: list[SeccionDetectada] = []
        seccion_actual: Optional[TipoSeccion] = None
        pagina_inicio_actual = 1
        titulo_actual = ""

        for i, linea in enumerate(lineas):
            linea_stripped = linea.strip()

            if linea_stripped.startswith("#"):
                titulo_candidato = re.sub(r"^#+\s*", "", linea_stripped).strip()
                tipo_detectado = self._clasificar_seccion(titulo_candidato)

                if tipo_detectado != TipoSeccion.DESCONOCIDO:
                    if seccion_actual is not None:
                        pagina_fin = max(1, int((i / max(len(lineas), 1)) * num_paginas))
                        secciones.append(SeccionDetectada(
                            tipo=seccion_actual,
                            titulo_detectado=titulo_actual,
                            pagina_inicio=pagina_inicio_actual,
                            pagina_fin=pagina_fin,
                            tiene_referencias=(seccion_actual == TipoSeccion.REFERENCIAS),
                        ))

                    seccion_actual = tipo_detectado
                    titulo_actual = titulo_candidato
                    pagina_inicio_actual = max(1, int((i / max(len(lineas), 1)) * num_paginas) + 1)

        if seccion_actual is not None:
            secciones.append(SeccionDetectada(
                tipo=seccion_actual,
                titulo_detectado=titulo_actual,
                pagina_inicio=pagina_inicio_actual,
                pagina_fin=num_paginas,
                tiene_referencias=(seccion_actual == TipoSeccion.REFERENCIAS),
            ))

        tiene_referencias = any(s.tipo == TipoSeccion.REFERENCIAS for s in secciones)

        advertencia = None
        if not tiene_referencias:
            advertencia = (
                "No se detectó una sección de Referencias o Bibliografía. "
                "La auditoría puede estar incompleta."
            )

        return EstructuraDocumentoResponse(
            documento_id=documento_id,
            total_paginas=num_paginas,
            secciones=secciones,
            tiene_seccion_referencias=tiene_referencias,
            advertencia=advertencia,
        )

    def _clasificar_seccion(self, titulo: str) -> TipoSeccion:
        titulo_lower = titulo.lower().strip()
        for tipo, patrones in PATRONES_SECCIONES.items():
            for patron in patrones:
                if re.match(patron, titulo_lower, re.IGNORECASE):
                    return tipo
        return TipoSeccion.DESCONOCIDO


pdf_service = PDFExtractionService()
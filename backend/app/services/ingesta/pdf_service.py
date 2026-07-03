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

PATRONES_SECCIONES: dict[TipoSeccion, list[str]] = {
    TipoSeccion.REFERENCIAS: [
        r"^lista\s+de\s+referencias?\s*(?:bibliogr[áa]ficas?)?\s*:?\s*$",
        r"^referencias?\s+bibliogr[áa]ficas?\s*:?\s*$",
        r"^referencias?\s*:?\s*$",
        r"^bibliograf[íi]a\s*:?\s*$",
        r"^references?\s*:?\s*$",
        r"^reference\s+list\s*:?\s*$",
        r"^works?\s+cited\s*:?\s*$",
        r"^fuentes?\s+bibliogr[áa]ficas?\s*:?\s*$",
        r"^fuentes?\s+de\s+consulta\s*:?\s*$",
        r"^\d+(?:\.\d+)*\.?\s+referencias?\s*(?:bibliogr[áa]ficas?)?\s*:?\s*$",
        r"^\d+(?:\.\d+)*\.?\s+bibliograf[íi]a(?:\s+\w+)?\s*:?\s*$",
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
    def __init__(self, codigo: str, mensaje: str, accion: str):
        self.codigo = codigo
        self.mensaje = mensaje
        self.accion = accion
        super().__init__(mensaje)


class PDFExtractionService:

    def validar_pdf(self, contenido: bytes, nombre_archivo: str) -> None:
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
        Extrae texto del PDF como Markdown.
        Cachea el resultado en disco para no re-procesar el mismo PDF.
        """
        import fitz

        # Siempre necesitamos el número de páginas
        doc = fitz.open(str(ruta_pdf))
        num_paginas = len(doc)
        doc.close()

        # Verificar cache
        ruta_cache = ruta_pdf.with_suffix('.md')
        if ruta_cache.exists():
            logger.info("pdf_desde_cache", paginas=num_paginas, ruta=str(ruta_cache))
            texto_md = ruta_cache.read_text(encoding='utf-8')
            return texto_md, num_paginas

        # Extraer con pymupdf4llm
        try:
            import pymupdf4llm

            inicio = time.perf_counter()

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

            # Guardar cache en disco
            ruta_cache.write_text(texto_md, encoding='utf-8')
            logger.info("pdf_cache_guardado", ruta=str(ruta_cache))

            return texto_md, num_paginas

        except PDFNoProcessableError:
            raise
        except Exception:
            raise PDFNoProcessableError(
                codigo="ERROR_LECTURA_PDF",
                mensaje="No se pudo leer el contenido del PDF.",
                accion="Verifica que el archivo no esté protegido con contraseña.",
            )

    # Heading line: markdown (#), bold (**text**), or plain numbered (7  TÍTULO).
    _RE_ES_ENCABEZADO = re.compile(
        r'^(?:#{1,3}\s|\*{1,2}.+\*{1,2}$|\d+(?:\.\d+)*\.?\s)',
    )

    @staticmethod
    def _normalizar_titulo(linea: str) -> str:
        """Strips # heading markers and all * bold/italic markers from a line."""
        linea = re.sub(r"^#+\s*", "", linea).strip()
        linea = re.sub(r"\*+", " ", linea).strip()
        linea = re.sub(r"\s{2,}", " ", linea)
        return linea

    def detectar_secciones(
        self,
        texto_md: str,
        num_paginas: int,
        documento_id: str,
    ) -> EstructuraDocumentoResponse:
        lineas = texto_md.split("\n")
        secciones: list[SeccionDetectada] = []
        seccion_actual: Optional[TipoSeccion] = None
        pagina_inicio_actual = 1
        titulo_actual = ""

        for i, linea in enumerate(lineas):
            linea_stripped = linea.strip()

            if not self._RE_ES_ENCABEZADO.match(linea_stripped):
                continue

            titulo_candidato = self._normalizar_titulo(linea_stripped)
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
                pagina_inicio_actual = max(
                    1, int((i / max(len(lineas), 1)) * num_paginas) + 1
                )

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
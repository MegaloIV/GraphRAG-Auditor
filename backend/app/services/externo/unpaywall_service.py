"""
Servicio de búsqueda de PDFs gratuitos via Unpaywall API.
Unpaywall es gratuita, solo requiere un email válido.
Busca versiones legales: preprints, repositorios institucionales, etc.

Flujo:
1. Recibe un DOI
2. Consulta Unpaywall
3. Si hay PDF gratuito → descarga → extrae texto
4. Si no → retorna None (usaremos el abstract de CrossRef)
"""
import time
import httpx
import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.config import get_settings


logger = structlog.get_logger(__name__)
settings = get_settings()

UNPAYWALL_BASE_URL = "https://api.unpaywall.org/v2"
UNPAYWALL_EMAIL = settings.crossref_email


@dataclass
class ResultadoUnpaywall:
    tiene_pdf_gratuito: bool
    url_pdf: Optional[str] = None
    texto_completo: Optional[str] = None
    ruta_local: Optional[str] = None


class UnpaywallService:

    def __init__(self):
        self._cliente = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "GraphRAG-Auditor/0.1"}
        )

    def buscar_pdf_gratuito(self, doi: str) -> ResultadoUnpaywall:
        """
        Busca si existe una versión gratuita del paper por DOI.
        Si existe, descarga el PDF y extrae el texto.
        """
        if not doi:
            return ResultadoUnpaywall(tiene_pdf_gratuito=False)

        url = f"{UNPAYWALL_BASE_URL}/{doi}"
        params = {"email": UNPAYWALL_EMAIL}

        try:
            response = self._cliente.get(url, params=params)

            if response.status_code == 404:
                logger.info("unpaywall_doi_no_encontrado", doi=doi)
                return ResultadoUnpaywall(tiene_pdf_gratuito=False)

            response.raise_for_status()
            data = response.json()

            # Verificar si es open access
            if not data.get("is_oa", False):
                logger.info("unpaywall_no_open_access", doi=doi)
                return ResultadoUnpaywall(tiene_pdf_gratuito=False)

            # Buscar la mejor URL de PDF disponible
            url_pdf = self._extraer_mejor_url(data)
            if not url_pdf:
                logger.info("unpaywall_sin_url_pdf", doi=doi)
                return ResultadoUnpaywall(tiene_pdf_gratuito=False)

            logger.info("unpaywall_pdf_encontrado", doi=doi, url=url_pdf[:60])

            # Descargar y extraer texto
            texto, ruta = self._descargar_y_extraer(doi, url_pdf)

            return ResultadoUnpaywall(
                tiene_pdf_gratuito=True,
                url_pdf=url_pdf,
                texto_completo=texto,
                ruta_local=ruta,
            )

        except Exception as e:
            logger.error("unpaywall_error", doi=doi, error=str(e))
            return ResultadoUnpaywall(tiene_pdf_gratuito=False)

    def _extraer_mejor_url(self, data: dict) -> Optional[str]:
        """
        Extrae la mejor URL de PDF disponible.
        Prioriza: publisher PDF > repositorio institucional > preprint
        """
        # Primero intentar la URL de mejor OA location
        best_location = data.get("best_oa_location", {})
        if best_location:
            url = best_location.get("url_for_pdf") or best_location.get("url")
            if url:
                return url

        # Si no, iterar todas las ubicaciones OA
        oa_locations = data.get("oa_locations", [])
        for location in oa_locations:
            url = location.get("url_for_pdf")
            if url:
                return url

        return None

    def _descargar_y_extraer(
        self,
        doi: str,
        url_pdf: str,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Descarga el PDF y extrae su texto con pymupdf4llm.
        Retorna (texto_extraido, ruta_local).
        """
        # Crear carpeta de papers si no existe
        papers_dir = Path(settings.processed_dir) / "papers"
        papers_dir.mkdir(parents=True, exist_ok=True)

        # Nombre de archivo basado en DOI normalizado
        doi_normalizado = doi.replace("/", "_").replace(".", "_")
        ruta_pdf = papers_dir / f"{doi_normalizado}.pdf"
        ruta_txt = papers_dir / f"{doi_normalizado}.txt"

        # Si ya fue descargado antes, usar el cache
        if ruta_txt.exists():
            logger.info("unpaywall_usando_cache", doi=doi)
            return ruta_txt.read_text(encoding="utf-8"), str(ruta_txt)

        try:
            # Descargar PDF
            logger.info("unpaywall_descargando", url=url_pdf[:60])
            response = self._cliente.get(url_pdf)

            if response.status_code != 200:
                logger.warning("unpaywall_descarga_fallida", status=response.status_code)
                return None, None

            # Verificar que es un PDF real
            if not response.content.startswith(b"%PDF"):
                logger.warning("unpaywall_no_es_pdf", url=url_pdf[:60])
                return None, None

            # Guardar PDF
            ruta_pdf.write_bytes(response.content)

            # Extraer texto con pymupdf4llm
            import pymupdf4llm
            texto = pymupdf4llm.to_markdown(str(ruta_pdf))

            if len(texto.strip()) < 100:
                logger.warning("unpaywall_texto_muy_corto", doi=doi)
                ruta_pdf.unlink(missing_ok=True)
                return None, None

            # Guardar texto extraído
            ruta_txt.write_text(texto, encoding="utf-8")

            # Eliminar PDF para ahorrar espacio, conservar solo el texto
            ruta_pdf.unlink(missing_ok=True)

            logger.info(
                "unpaywall_texto_extraido",
                doi=doi,
                chars=len(texto),
                ruta=str(ruta_txt),
            )

            return texto, str(ruta_txt)

        except Exception as e:
            logger.error("unpaywall_extraccion_fallida", doi=doi, error=str(e))
            ruta_pdf.unlink(missing_ok=True)
            return None, None

    def cerrar(self):
        self._cliente.close()


# Singleton
unpaywall_service = UnpaywallService()
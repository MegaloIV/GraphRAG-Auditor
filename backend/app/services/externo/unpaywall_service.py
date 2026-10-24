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
import re
import time
import httpx
import structlog
from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings
from app.services.storage.supabase_storage_service import storage_service


logger = structlog.get_logger(__name__)
settings = get_settings()

UNPAYWALL_BASE_URL = "https://api.unpaywall.org/v2"
# Un artículo académico completo no baja de esto; por debajo es portada o error.
_MIN_CHARS_PAPER = 3000
UNPAYWALL_EMAIL = settings.crossref_email

# Los DOIs "10.48550/arXiv.XXXX.YYYYY" no están registrados en Unpaywall, que
# devuelve 404 aunque el paper sea íntegramente abierto. Se resuelven contra
# arXiv directamente.
_RE_DOI_ARXIV = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)


def _url_pdf_arxiv(doi: str) -> Optional[str]:
    m = _RE_DOI_ARXIV.match((doi or "").strip())
    return f"https://arxiv.org/pdf/{m.group(1)}" if m else None


# Motivos por los que no se pudo obtener el texto de un paper. Hasta ahora
# solo quedaban en el log, así que el informe decía "no disponible" sin
# distinguir un DOI inventado de una editorial que bloquea la descarga —que
# son problemas de naturaleza opuesta: uno es un error del tesista y el otro
# una limitación del sistema.
MOTIVO_NO_OPEN_ACCESS      = "no_open_access"
MOTIVO_SIN_URL_PDF         = "sin_url_de_pdf"
MOTIVO_BLOQUEADO_EDITORIAL = "bloqueado_por_la_editorial"
MOTIVO_DESAFIO_ANTIBOT     = "desafio_antibot"
MOTIVO_NO_ES_PDF           = "el_enlace_no_es_un_pdf"
MOTIVO_PDF_SIN_TEXTO       = "pdf_sin_texto_extraible"
MOTIVO_NO_EN_CATALOGO      = "doi_no_esta_en_unpaywall"
MOTIVO_DESCARGA_FALLIDA    = "descarga_fallida"

# Motivos que no nacen de la descarga, pero comparten el mismo campo.
MOTIVO_DOI_INEXISTENTE = "doi_inexistente"
MOTIVO_SIN_DOI         = "sin_doi_y_no_resuelta_por_titulo"
MOTIVO_SIN_PDF_REPO    = "sin_pdf_en_repositorio"

_DESCRIPCIONES = {
    MOTIVO_DOI_INEXISTENTE: "El DOI citado no existe en CrossRef: revisa si está mal transcrito o es inventado.",
    MOTIVO_SIN_DOI:         "La referencia no declara DOI y no se encontró en ningún catálogo por su título.",
    MOTIVO_SIN_PDF_REPO:    "El DOI es válido (repositorio institucional), pero no publica un PDF descargable.",
    # Fuentes web citadas sin DOI (documentación de producto, guías, blogs).
    "url_invalida":              "La referencia declara una URL que no es válida.",
    "bloqueado_por_el_sitio":    "La página web rechaza la descarga automática.",
    "el_enlace_no_es_una_pagina": "El enlace no devuelve una página web legible.",
    "html_ilegible":             "La página no se pudo interpretar.",
    "pagina_sin_texto_util":     "La página no tiene texto suficiente para verificar nada (probablemente carga su contenido con JavaScript).",
    MOTIVO_NO_OPEN_ACCESS:      "El paper es de pago; no hay versión abierta.",
    MOTIVO_SIN_URL_PDF:         "Es abierto pero ningún repositorio publica el PDF.",
    MOTIVO_BLOQUEADO_EDITORIAL: "La editorial rechaza la descarga automática (HTTP 403).",
    MOTIVO_DESAFIO_ANTIBOT:     "El sitio exige verificación anti-bot.",
    MOTIVO_NO_ES_PDF:           "El enlace lleva a una página, no al PDF.",
    MOTIVO_PDF_SIN_TEXTO:       "El PDF no tiene texto extraíble; probablemente un escaneo sin OCR.",
    MOTIVO_NO_EN_CATALOGO:      "El DOI no está registrado en Unpaywall.",
    MOTIVO_DESCARGA_FALLIDA:    "La descarga falló por un error de red o del servidor.",
    # Validaciones de que la fuente conseguida es la citada.
    "doi_no_corresponde":       "El DOI escrito en la bibliografía pertenece a otro trabajo: no coincide con el título ni con los autores citados.",
    "texto_no_corresponde":     "Se obtuvo un documento, pero no corresponde al título citado; no se usó como evidencia.",
    "url_rota":                 "La URL de la bibliografía no existe (error 404): probablemente está mal escrita.",
    "url_generica":             "La URL de la bibliografía es solo la portada del sitio, no el documento citado.",
    "contenido_no_corresponde": "La página enlazada no trata del documento citado.",
    "pdf_restringido":          "El repositorio no permite descargar el PDF (acceso restringido o embargo).",
    "libro_sin_acceso":         "Es un libro: el enlace lleva a su ficha de catálogo (Google Books, tienda), no al texto.",
}


def describir_motivo(motivo: str | None) -> str:
    """Texto legible del motivo, para el informe."""
    if not motivo:
        return ""
    return _DESCRIPCIONES.get(motivo, motivo.replace("_", " "))


@dataclass
class ResultadoUnpaywall:
    tiene_pdf_gratuito: bool
    url_pdf: Optional[str] = None
    texto_completo: Optional[str] = None
    ruta_local: Optional[str] = None
    # Por qué no se consiguió, cuando tiene_pdf_gratuito es False.
    motivo: Optional[str] = None
    # El PDF original, para respaldarlo (Drive) junto al texto.
    pdf: Optional[bytes] = None


class UnpaywallService:

    def __init__(self):
        self._cliente = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                # Varias editoriales (MDPI, Wiley, SAGE, revistas OJS) responden
                # 403 a un User-Agent de script aunque el artículo sea abierto.
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                ),
                "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                "From": UNPAYWALL_EMAIL or "",
            },
        )

    def buscar_pdf_gratuito(
        self, doi: str, titulo_tesis: str = ""
    ) -> ResultadoUnpaywall:
        """
        Busca una versión gratuita del paper y extrae su texto.

        Se prueban TODAS las ubicaciones conocidas, no solo la mejor: una sola
        URL falla demasiado —la editorial responde 403, el enlace apunta a la
        landing page en vez del PDF, o el DOI ni siquiera está en Unpaywall—
        y rendirse con la primera dejaba sin texto a papers abiertos.
        """
        if not doi:
            return ResultadoUnpaywall(tiene_pdf_gratuito=False)

        objeto_txt = f"papers/{doi.replace('/', '_').replace('.', '_')}.txt"
        texto_cache = storage_service.leer_texto(objeto_txt)
        if texto_cache is not None:
            logger.info("pdf_desde_cache", doi=doi)
            return ResultadoUnpaywall(
                tiene_pdf_gratuito=True, texto_completo=texto_cache, ruta_local=objeto_txt
            )

        candidatas, motivo_catalogo = self._urls_candidatas(doi)
        if not candidatas:
            return ResultadoUnpaywall(tiene_pdf_gratuito=False, motivo=motivo_catalogo)

        ultimo_motivo = None
        for url_pdf in candidatas:
            texto, ruta, motivo, pdf = self._descargar_y_extraer(doi, url_pdf, titulo_tesis)
            ultimo_motivo = motivo or ultimo_motivo
            if texto:
                logger.info(
                    "pdf_obtenido", doi=doi, url=url_pdf[:70], chars=len(texto),
                    intentos=candidatas.index(url_pdf) + 1,
                )
                return ResultadoUnpaywall(
                    tiene_pdf_gratuito=True,
                    url_pdf=url_pdf,
                    texto_completo=texto,
                    ruta_local=ruta,
                    pdf=pdf,
                )

        logger.info(
            "pdf_no_obtenido", doi=doi, urls_probadas=len(candidatas),
            motivo=ultimo_motivo or MOTIVO_DESCARGA_FALLIDA,
        )
        return ResultadoUnpaywall(
            tiene_pdf_gratuito=False, motivo=ultimo_motivo or MOTIVO_DESCARGA_FALLIDA
        )

    def _urls_candidatas(self, doi: str) -> tuple[list[str], Optional[str]]:
        """
        URLs donde puede estar el PDF, de más a menos probable y sin repetir.
        arXiv va primero porque sus DOIs no están en Unpaywall (404) aunque el
        texto sea totalmente público.
        """
        candidatas: list[str] = []
        motivo: Optional[str] = None

        def agregar(url: Optional[str]) -> None:
            if url and url not in candidatas:
                candidatas.append(url)

        agregar(_url_pdf_arxiv(doi))

        try:
            response = self._cliente.get(
                f"{UNPAYWALL_BASE_URL}/{doi}", params={"email": UNPAYWALL_EMAIL}
            )
            if response.status_code == 404:
                logger.info("unpaywall_doi_no_encontrado", doi=doi)
                motivo = MOTIVO_NO_EN_CATALOGO
            else:
                response.raise_for_status()
                data = response.json()
                if not data.get("is_oa", False):
                    logger.info("unpaywall_no_open_access", doi=doi)
                    motivo = MOTIVO_NO_OPEN_ACCESS
                ubicaciones = [data.get("best_oa_location") or {}, *(data.get("oa_locations") or [])]
                for ubicacion in ubicaciones:
                    agregar(ubicacion.get("url_for_pdf"))
                # Las landing pages van al final: a veces redirigen al PDF.
                for ubicacion in ubicaciones:
                    agregar(ubicacion.get("url"))
        except Exception as e:
            logger.warning("unpaywall_error", doi=doi, error=str(e))
            motivo = motivo or MOTIVO_DESCARGA_FALLIDA

        if not candidatas and motivo is None:
            motivo = MOTIVO_SIN_URL_PDF
        return candidatas, motivo

    def _descargar_y_extraer(
        self,
        doi: str,
        url_pdf: str,
        titulo_tesis: str = "",
    ) -> tuple[Optional[str], Optional[str], Optional[str], Optional[bytes]]:
        """
        Descarga el PDF y extrae su texto con pymupdf4llm.
        El texto extraído se cachea en Supabase Storage (objeto papers/<doi>.txt);
        el PDF se procesa en un archivo temporal y no se conserva.
        Retorna (texto_extraido, clave_objeto, motivo, pdf).
        """
        doi_normalizado = doi.replace("/", "_").replace(".", "_")
        objeto_txt = f"papers/{doi_normalizado}.txt"

        try:
            # Descargar PDF
            logger.info("unpaywall_descargando", url=url_pdf[:60])
            response = self._cliente.get(url_pdf)

            if response.status_code != 200:
                logger.warning("unpaywall_descarga_fallida", status=response.status_code)
                if response.status_code == 403:
                    return None, None, MOTIVO_BLOQUEADO_EDITORIAL, None
                if response.status_code in (202, 429, 503):
                    return None, None, MOTIVO_DESAFIO_ANTIBOT, None
                return None, None, MOTIVO_DESCARGA_FALLIDA, None

            # Verificar que es un PDF real
            if not response.content.startswith(b"%PDF"):
                # Muchas revistas usan OJS, donde el enlace del DOI lleva a la
                # ficha del artículo y el PDF cuelga de /article/download/<id>/
                # <galerada> — una ruta que no aparece como enlace con la
                # palabra "pdf", así que rastrear la página no la encuentra.
                pdf_ojs = self._pdf_desde_ojs(response)
                if pdf_ojs is None:
                    logger.warning("unpaywall_no_es_pdf", url=url_pdf[:60])
                    return None, None, MOTIVO_NO_ES_PDF, None
                response = pdf_ojs

            # Extracción protegida: los documentos de cientos de páginas
            # disparan la memoria de pymupdf4llm.
            from app.services.externo.fuentes_abiertas import pdf_a_texto
            texto = pdf_a_texto(response.content) or ""

            if len(texto.strip()) < _MIN_CHARS_PAPER:
                # Portadas, avisos de "access denied" y páginas de error
                # pasaban el umbral anterior de 100 caracteres y se indexaban
                # como si fueran el paper: el juez emitía veredictos sobre un
                # texto que no era el de la fuente citada.
                logger.warning(
                    "pdf_descartado_texto_insuficiente", doi=doi, chars=len(texto.strip())
                )
                return None, None, MOTIVO_PDF_SIN_TEXTO, None

            # Guardar texto extraído en la nube. El respaldo en Drive lo hace
            # la verificación, para todas las vías de adquisición por igual.
            storage_service.subir_texto(objeto_txt, texto)

            logger.info(
                "unpaywall_texto_extraido",
                doi=doi,
                chars=len(texto),
                objeto=objeto_txt,
            )

            return texto, objeto_txt, None, response.content

        except Exception as e:
            logger.error("unpaywall_extraccion_fallida", doi=doi, error=str(e))
            return None, None, MOTIVO_DESCARGA_FALLIDA, None

    def _pdf_desde_ojs(self, respuesta) -> Optional[object]:
        """
        Busca la galerada PDF de un artículo servido por OJS.

        Devuelve la respuesta con el PDF, o None si la página no es un OJS o
        no expone ninguna.
        """
        try:
            texto = respuesta.text
        except Exception:
            return None
        parejas = set(re.findall(r"/article/(?:view|download)/(\d+)/(\d+)", texto))
        if not parejas:
            return None

        base = str(respuesta.url).split("/article/")[0]
        for articulo, galerada in list(parejas)[:4]:
            try:
                candidato = self._cliente.get(
                    f"{base}/article/download/{articulo}/{galerada}"
                )
            except Exception:
                continue
            if candidato.status_code == 200 and candidato.content.startswith(b"%PDF"):
                logger.info("pdf_hallado_en_ojs", url=str(candidato.url)[:80])
                return candidato
        return None

    def cerrar(self):
        self._cliente.close()


# Singleton
unpaywall_service = UnpaywallService()
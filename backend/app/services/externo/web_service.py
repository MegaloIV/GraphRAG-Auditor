"""
Descarga de fuentes web citadas en la tesis.

No toda bibliografía es académica. Las tesis de ingeniería aplicada citan
documentación de producto, entradas de blog técnico y guías de proveedor:
fuentes sin DOI, que no están en ningún catálogo y para las que no existe un
PDF. Una tesis del lote tenía las 22 referencias así, y el sistema no pudo
verificar ni una sola cita.

Esas fuentes sí son accesibles —son páginas públicas— y su texto se puede
indexar igual que el de un paper. Este servicio las descarga y extrae su
contenido legible.
"""
import re
from dataclasses import dataclass
from typing import Optional

import httpx
import structlog
from lxml import html as lxml_html

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Elementos que nunca son contenido: menús, banners, scripts y demás cromo.
_ETIQUETAS_RUIDO = (
    "script", "style", "nav", "header", "footer", "aside", "noscript",
    "form", "iframe", "svg", "button",
)
# Contenedores donde suele vivir el texto real, en orden de preferencia.
_SELECTORES_CONTENIDO = (
    "main", "article", "[role=main]", "#main-content", ".content",
    "#content", ".post-content", ".markdown-body",
)
_MIN_CHARS_UTILES = 800


@dataclass
class ResultadoWeb:
    obtenido: bool
    texto: Optional[str] = None
    titulo: Optional[str] = None
    motivo: Optional[str] = None


def _limpiar(texto: str) -> str:
    texto = re.sub(r"[ \t\xa0]+", " ", texto)
    texto = re.sub(r"\n\s*\n\s*\n+", "\n\n", texto)
    return "\n".join(l.strip() for l in texto.splitlines()).strip()


class WebService:

    def __init__(self):
        self._cliente = httpx.Client(
            timeout=25.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            },
        )

    def obtener_texto(self, url: str) -> ResultadoWeb:
        """Descarga una página y devuelve su texto legible."""
        if not url or not url.lower().startswith(("http://", "https://")):
            return ResultadoWeb(obtenido=False, motivo="url_invalida")

        try:
            respuesta = self._cliente.get(url)
        except Exception as e:
            logger.warning("web_descarga_fallida", url=url[:70], error=str(e)[:90])
            return ResultadoWeb(obtenido=False, motivo="descarga_fallida")

        if respuesta.status_code != 200:
            logger.info("web_http_error", url=url[:70], status=respuesta.status_code)
            # Un 404/410 es una URL que no existe: casi siempre una errata del
            # tesista. Se reporta como tal en vez de confundirla con un fallo
            # de red; el sistema no la corrige (lo haría a espaldas del autor).
            if respuesta.status_code in (404, 410):
                motivo = "url_rota"
            elif respuesta.status_code in (401, 403):
                motivo = "bloqueado_por_el_sitio"
            else:
                motivo = "descarga_fallida"
            return ResultadoWeb(obtenido=False, motivo=motivo)

        tipo = respuesta.headers.get("content-type", "")
        if "html" not in tipo and "text" not in tipo:
            return ResultadoWeb(obtenido=False, motivo="el_enlace_no_es_una_pagina")

        try:
            # httpx resuelve la codificación con la cabecera y el meta charset;
            # pasarle los bytes crudos a lxml hacía que adivinara mal y el texto
            # llegara con mojibake ("Â¿QuÃ© es" por "¿Qué es"), que además
            # ensuciaría los embeddings.
            arbol = lxml_html.fromstring(
                respuesta.text.encode("utf-8"),
                parser=lxml_html.HTMLParser(encoding="utf-8"),
            )
        except Exception:
            return ResultadoWeb(obtenido=False, motivo="html_ilegible")

        for etiqueta in _ETIQUETAS_RUIDO:
            for nodo in arbol.xpath(f"//{etiqueta}"):
                nodo.getparent().remove(nodo)

        titulos = arbol.xpath("//title/text()")
        titulo = (titulos[0].strip() if titulos else "")[:250]

        # Se prefiere el contenedor principal; si no existe, todo el cuerpo.
        texto = ""
        for selector in _SELECTORES_CONTENIDO:
            try:
                nodos = arbol.cssselect(selector)
            except Exception:
                continue
            if nodos:
                texto = _limpiar(nodos[0].text_content())
                if len(texto) >= _MIN_CHARS_UTILES:
                    break
        if len(texto) < _MIN_CHARS_UTILES:
            texto = _limpiar(arbol.text_content())

        if len(texto) < _MIN_CHARS_UTILES:
            logger.info("web_sin_texto_util", url=url[:70], chars=len(texto))
            return ResultadoWeb(obtenido=False, titulo=titulo, motivo="pagina_sin_texto_util")

        logger.info("web_texto_obtenido", url=url[:70], chars=len(texto))
        return ResultadoWeb(obtenido=True, texto=texto, titulo=titulo)

    def cerrar(self) -> None:
        self._cliente.close()


web_service = WebService()

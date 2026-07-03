"""Normalización de DOIs compartida por verificación e importación Zotero."""
import re

_RE_PREFIJO_DOI = re.compile(r'^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*', re.IGNORECASE)


def normalizar_doi(doi: str | None) -> str:
    """
    Forma canónica para comparar DOIs: sin prefijo de URL/'doi:', sin espacios,
    en minúsculas. Retorna "" si no hay DOI.
    """
    if not doi:
        return ""
    limpio = _RE_PREFIJO_DOI.sub("", doi.strip())
    return limpio.strip().strip("/").lower()


def doi_para_chunks(doi: str) -> str:
    """Clave del DOI en la tabla papers_chunks (mismo formato que el resto del código)."""
    return doi.replace("/", "_").replace(".", "_")

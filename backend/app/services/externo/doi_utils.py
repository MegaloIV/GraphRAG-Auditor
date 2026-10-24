"""Normalización de DOIs compartida por verificación e importación Zotero."""
import re

_RE_PREFIJO_DOI = re.compile(r'^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*', re.IGNORECASE)

# Un DOI siempre empieza por "10.<registrante>/". Lo que no cumpla esto no se
# puede consultar en CrossRef ni en Unpaywall, así que guardarlo en el campo
# DOI solo produce búsquedas que fallan siempre.
_RE_DOI_VALIDO = re.compile(r'^10\.\d{4,9}/\S+$')

# Identificador de arXiv suelto ("arXiv:1512.03385v1 [cs.CV]"): no es un DOI,
# pero tiene uno canónico, y el paper es público. Convertirlo lo vuelve
# descargable en vez de descartarlo.
_RE_ID_ARXIV = re.compile(
    r'^ar[xX]iv[:\s]\s*(\d{4}\.\d{4,5})(?:v\d+)?', re.IGNORECASE
)


def normalizar_doi(doi: str | None) -> str:
    """
    Forma canónica para comparar DOIs: sin prefijo de URL/'doi:', sin espacios,
    en minúsculas. Retorna "" si el valor no es un DOI.

    Los identificadores de arXiv se convierten a su DOI oficial; cualquier otra
    cosa se descarta en vez de propagarse como un DOI que no existe.
    """
    if not doi:
        return ""
    limpio = _RE_PREFIJO_DOI.sub("", doi.strip()).strip().strip("/").lower()
    if not limpio:
        return ""

    arxiv = _RE_ID_ARXIV.match(limpio)
    if arxiv:
        return f"10.48550/arxiv.{arxiv.group(1)}"

    return limpio if _RE_DOI_VALIDO.match(limpio) else ""


def doi_para_chunks(doi: str) -> str:
    """Clave del DOI en la tabla papers_chunks (mismo formato que el resto del código)."""
    return doi.replace("/", "_").replace(".", "_")

"""
Utilidades de limpieza de texto compartidas.

El markdown extraído de los PDFs arrastra ruido (negritas, encabezados,
separadores, saltos de línea del layout original). Se limpia con una sola
función para que el índice vectorial, el juez LLM, las cartillas y el Excel
vean el mismo texto corrido.
"""
import re

_RE_RUIDO_MD = re.compile(r"\*{1,2}|`+|^#{1,6}\s*", re.MULTILINE)
_RE_SEPARADOR = re.compile(r"^[\s_\-—=]{3,}$", re.MULTILINE)
_RE_SALTOS = re.compile(r"\s*\n\s*")
# Invisibles típicos de PDF: zero-width space/joiner, BOM, soft hyphen.
_RE_INVISIBLES = re.compile("[\\u200b\\u200c\\u200d\\ufeff\\u00ad]")


def limpiar_fragmento(texto: str) -> str:
    """Quita marcado markdown e invisibles y colapsa saltos en texto corrido."""
    texto = _RE_INVISIBLES.sub("", texto or "")
    texto = _RE_RUIDO_MD.sub("", texto)
    texto = _RE_SEPARADOR.sub(" ", texto)
    texto = _RE_SALTOS.sub(" ", texto)
    return re.sub(r"\s{2,}", " ", texto).strip()

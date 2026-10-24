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


# ── Aplanado con trazabilidad de posiciones ──────────────────────────────────
# El markdown de un PDF parte las citas en dos ("(LeCun et\nal., 2015)") y el
# layout mete saltos en cualquier parte. Detectar sobre el texto crudo obliga a
# que cada regex tolere \n en todos sus huecos, cosa que nunca se cumple del
# todo. En vez de eso se aplana una vez —espacios colapsados, markdown fuera—
# guardando de qué posición del original salió cada carácter, de modo que un
# match sobre el texto plano se puede devolver a coordenadas del original.

_INVISIBLES = frozenset("​‌‍﻿­")
_MARCADO_MD = frozenset("*`|")
# Regla de tabla/separador markdown ("---", "___"): ruido puro en el claim.
_RE_REGLA_MD = re.compile(r"[-_=]{3,}")
# Enlace markdown hacia una URL: se conserva el texto visible y se descarta la
# sintaxis. El plugin de Zotero para Google Docs envuelve cada cita en un
# enlace y el PDF la parte en dos —"[(Alaghbari et al.,](https://zotero…)
# [2023).](https://zotero…)"—, así que sin esto la cita no se detecta.
_RE_ENLACE_MD = re.compile(r"\[([^\]\n]*)\]\((?:https?://|www\.)[^)\s]*\)")


class TextoPlano:
    """Texto normalizado + mapa posicional hacia el texto original."""

    __slots__ = ("plano", "original", "_mapa")

    def __init__(self, plano: str, original: str, mapa: list[int]):
        self.plano = plano
        self.original = original
        self._mapa = mapa

    def a_original(self, inicio: int, fin: int) -> tuple[int, int]:
        """Traduce un span [inicio, fin) del texto plano al texto original."""
        if not self._mapa or inicio >= len(self._mapa):
            return (0, 0)
        fin = max(inicio + 1, min(fin, len(self._mapa)))
        return (self._mapa[inicio], self._mapa[fin - 1] + 1)

    def __len__(self) -> int:
        return len(self.plano)


def aplanar(texto: str) -> TextoPlano:
    """
    Colapsa espacios/saltos, descarta invisibles y marcado markdown, y une las
    palabras cortadas con guion al final del renglón. Cada carácter del
    resultado recuerda su posición en `texto`.
    """
    caracteres: list[str] = []
    mapa: list[int] = []
    texto = texto or ""
    i, n = 0, len(texto)

    # Posiciones de la sintaxis de enlace ("[" y "](url)") que se saltan;
    # el texto visible del enlace se procesa como cualquier otro.
    omitir: dict[int, int] = {}
    for m in _RE_ENLACE_MD.finditer(texto):
        omitir[m.start()] = m.start() + 1          # "["
        omitir[m.end(1)] = m.end()                 # "](url)"

    while i < n:
        c = texto[i]

        if i in omitir:
            i = omitir[i]
            continue

        if c in _INVISIBLES or c in _MARCADO_MD:
            i += 1
            continue

        # Corridas de guiones/guiones bajos: separador de tabla, no contenido.
        regla = _RE_REGLA_MD.match(texto, i)
        if regla:
            i = regla.end()
            continue

        if c.isspace():
            j = i
            while j < n and texto[j].isspace():
                j += 1
            # "Johnson-\nLaird" → "Johnson-Laird": el guion de corte se queda
            # pegado, sin espacio intermedio.
            if caracteres and caracteres[-1] == "-":
                i = j
                continue
            if caracteres:          # nunca se abre con espacio
                caracteres.append(" ")
                mapa.append(i)
            i = j
            continue

        caracteres.append(c)
        mapa.append(i)
        i += 1

    while caracteres and caracteres[-1] == " ":
        caracteres.pop()
        mapa.pop()

    return TextoPlano("".join(caracteres), texto or "", mapa)

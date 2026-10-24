"""
Emparejamiento automático de un PDF con la referencia que le corresponde.

Cargar los papers de uno en uno, eligiendo a mano la referencia de cada
archivo, es el cuello de botella real del sistema: las editoriales bloquean la
descarga automática y la carga manual termina siendo el camino principal.

Un PDF académico casi siempre lleva su propio DOI impreso en la primera
página, y si no, su título. Con eso se decide solo a qué referencia pertenece,
y soltar cuarenta archivos de golpe pasa a ser un trámite de dos minutos.
"""
import re

import structlog
from unidecode import unidecode

logger = structlog.get_logger(__name__)

# DOI tal como aparece impreso: "doi:10.1234/abc", "https://doi.org/10.1234/abc"
# o suelto. El corte final evita arrastrar la puntuación de la línea.
_RE_DOI = re.compile(
    r"(?:doi\s*:?\s*|https?://(?:dx\.)?doi\.org/)?"
    r"\b(10\.\d{4,9}/[^\s\"'<>,;)\]]+)",
    re.IGNORECASE,
)
# El PDF se lee como markdown, así que el DOI puede venir envuelto en
# marcadores de formato ("...2022040111.**"). Sin quitarlos, el DOI
# correcto no coincide nunca con el almacenado.
_RE_BASURA_FINAL = re.compile(r"[.,;:)\]\}>*_`]+$")

# El DOI del paper está al principio; más adentro aparecen los de su propia
# bibliografía, que no son el del documento.
_CHARS_CABECERA = 3000
_MIN_SIMILITUD_TITULO = 0.80
_MIN_PALABRAS_TITULO = 4


def extraer_doi(texto: str) -> str | None:
    """DOI impreso en la cabecera del paper, o None."""
    for m in _RE_DOI.finditer((texto or "")[:_CHARS_CABECERA]):
        doi = _RE_BASURA_FINAL.sub("", m.group(1)).lower()
        if len(doi) > 7:
            return doi
    return None


def _palabras(texto: str) -> set[str]:
    limpio = re.sub(r"[^a-z0-9\s]", " ", unidecode((texto or "").lower()))
    return {p for p in limpio.split() if len(p) > 2}


def _contencion(titulo: str, cabecera: str) -> float:
    """
    Qué proporción del título aparece en la cabecera del PDF.

    No es Jaccard: se compara un título de ocho palabras contra tres mil
    caracteres, así que la unión sería enorme y hundiría cualquier
    coincidencia real. Lo que interesa es si el título ESTÁ ahí.
    """
    palabras_titulo = _palabras(titulo)
    # Un título de dos o tres palabras aparece por casualidad en cualquier
    # cabecera; no identifica el documento.
    if len(palabras_titulo) < _MIN_PALABRAS_TITULO:
        return 0.0
    return len(palabras_titulo & _palabras(cabecera)) / len(palabras_titulo)


def _frase(texto: str) -> str:
    """Texto comparable como frase: sin acentos, sin puntuación, un solo espacio."""
    return " " + re.sub(r"[^a-z0-9]+", " ", unidecode((texto or "").lower())).strip() + " "


def _posicion_literal(titulo: str, cabecera: str) -> int:
    """
    Dónde aparece el título completo y seguido en la cabecera, o -1.

    Interesa la posición y no solo la presencia: la portada de una tesis
    menciona literalmente otros trabajos además del propio, y el título del
    documento es el que va arriba.
    """
    return _frase(cabecera).find(_frase(titulo).strip())


def emparejar(texto_pdf: str, referencias: list[dict]) -> tuple[dict | None, str]:
    """
    Decide a qué referencia pertenece el PDF.

    `referencias` es [{"ref_id", "titulo", "doi", ...}]. Devuelve
    (referencia, metodo) con metodo en {"doi", "titulo", "sin_coincidencia"}.

    Primero por DOI, que es inequívoco. Si el PDF no lo trae, por título
    contra la cabecera del documento, exigiendo una coincidencia alta y
    ÚNICA: emparejar mal un paper es peor que no emparejarlo, porque la
    auditoría verificaría la cita contra el texto equivocado.
    """
    if not texto_pdf or not referencias:
        return None, "sin_coincidencia"

    doi_pdf = extraer_doi(texto_pdf)
    if doi_pdf:
        for ref in referencias:
            doi_ref = (ref.get("doi") or "").strip().lower()
            if doi_ref and doi_ref == doi_pdf:
                return ref, "doi"

    cabecera = (texto_pdf or "")[:_CHARS_CABECERA]
    puntuadas = sorted(
        ((_contencion(ref.get("titulo") or "", cabecera), ref) for ref in referencias),
        key=lambda x: x[0],
        reverse=True,
    )
    if not puntuadas:
        return None, "sin_coincidencia"

    mejor, ref = puntuadas[0]
    if mejor < _MIN_SIMILITUD_TITULO:
        return None, "sin_coincidencia"
    # Un segundo candidato casi igual de bueno significa que no se distingue.
    if len(puntuadas) > 1 and puntuadas[1][0] > mejor * 0.9:
        # ...salvo que el empate sea un artefacto de contar palabras sueltas.
        # Un título corto y genérico ("Natural Language to SQL Interfaces for
        # Databases") tiene todas sus palabras esparcidas por la cabecera de
        # cualquier paper del área y puntúa 1.0 sin ser el documento. Si de los
        # empatados solo uno aparece como frase literal, ese es el paper.
        empatados = [r for p, r in puntuadas if p > mejor * 0.9]
        literales = sorted(
            ((_posicion_literal(r.get("titulo") or "", cabecera), i, r)
             for i, r in enumerate(empatados)),
            key=lambda x: x[0],
        )
        literales = [t for t in literales if t[0] >= 0]
        if literales:
            return literales[0][2], "titulo"
        logger.debug("emparejamiento_pdf_ambiguo", mejor=round(mejor, 2),
                     empatados=len(empatados))
        return None, "sin_coincidencia"

    return ref, "titulo"

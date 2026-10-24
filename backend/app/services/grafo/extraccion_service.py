import json
import re
import uuid
import structlog

from app.services.llm.openai_client import llm_service
from app.core.texto import aplanar
from app.models.grafo import ReferenciaAPA, CitaEnTexto, TipoCita

logger = structlog.get_logger(__name__)

# Compiled from the original inline patron in extraer_referencias.
# Covers markdown headings, bold, and simple numbered formats.
# Decimal section numbers (e.g. "3.1 Referencias"), "Lista de referencias",
# and optional trailing colon are also handled.
_RE_REFERENCIAS = re.compile(
    r'^(\*{1,2})?\s*#{0,3}\s*'
    r'(lista\s+de\s+referencias?\s*(?:bibliogr[áa]ficas?)?'
    r'|referencias?\s*bibliogr[áa]ficas?|referencias?|bibliograf[íi]a|references?'
    r'|reference\s+list'
    r'|works?\s+cited|fuentes?\s+bibliogr[áa]ficas?|fuentes?\s+de\s+consulta'
    r'|\d+(?:\.\d+)*\.?\s+referencias?(?:\s+bibliogr[áa]ficas?)?|\d+(?:\.\d+)*\.?\s+bibliograf[íi]a)'
    r'\s*:?\s*(\*{1,2})?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

# Numbered heading with optional word suffix: "7.  Referencias bibliográficas"
# Handles single and decimal section numbers (e.g. "3.1. Referencias") and
# roman numerals ("VIII. REFERENCIAS BIBLIOGRÁFICAS"): sin ellos, la
# bibliografía de una tesis numerada en romanos no se detectaba y sus entradas
# se analizaban como si fueran cuerpo del texto.
_RE_REFERENCIAS_NUMERADA = re.compile(
    r'^(?:\d+(?:\.\d+)*|(?-i:[IVXLC]+))\.?\s+'
    r'(?:referencias?(?:\s+\w+)?|bibliograf[íi]a(?:\s+\w+)?)\s*:?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

# Matches the start of an Annexes/Appendix section as a standalone heading line.
# Covers: markdown # / ## / ###, **bold**, plain text, and all-caps variants.
_RE_INICIO_ANEXOS = re.compile(
    r'^(?:#{1,3}\s+)?(?:\*{1,2})?\s*'
    r'(Anexos?|Ap[eé]ndices?|Appendix)'
    r'\s*(?:\*{1,2})?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

# Used in the normalization pass of _truncar_antes_de_anexos.
# Matches after all * and leading numbers are stripped from the line.
_RE_KEYWORD_ANEXOS = re.compile(
    r'^\s*(?:\d+\.?\s+)?(Anexos?|Ap[eé]ndices?|Appendix)\s*$',
    re.IGNORECASE,
)

# Matches plain-text numbered annex lines: "Anexo 2." or "Anexo 3:"
# Only used after the 70% position threshold to avoid false positives in the body.
_RE_ANEXO_NUMERADO = re.compile(r'^Anexo\s+\d+[\.\:]', re.MULTILINE)

# Sentence boundary: after .!? followed by whitespace and a Spanish/latin uppercase letter.
_RE_ORACION_FRASE = re.compile(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ])')

# ── Filtrado por índice: secciones donde NO se buscan citas ──────────────────
# Metodología, resultados y conclusiones/recomendaciones no se auditan para
# citas. La sección de referencias solo alimenta extraer_referencias y ya se
# excluye aparte; los anexos se truncan antes.
_RE_SECCION_EXCLUIDA = re.compile(
    r'\b(marco\s+metodol[oó]gico|dise[ñn]o\s+metodol[oó]gico|metodolog[íi]a'
    r'|m[eé]todos?\b|methodology|methods?\b'
    r'|resultados?\b|results?\b'
    r'|conclusi[oó]n(?:es)?\b|conclusions?\b'
    r'|recomendaci[oó]n(?:es)?\b|recommendations?\b)',
    re.IGNORECASE,
)

# Secciones que se conservan aunque cuelguen de un capítulo excluido
# (p. ej. "Hipótesis y variables" dentro del marco metodológico).
_RE_SECCION_PROTEGIDA = re.compile(r'hip[oó]tesis|variables?\b', re.IGNORECASE)

# Encabezado del índice / tabla de contenido del documento.
_RE_ENCABEZADO_INDICE = re.compile(
    r'^(?:#{1,3}\s*)?'
    r'([íi]ndice(?:\s+(?:general|de\s+contenidos?))?|tabla\s+de\s+contenidos?'
    r'|contenidos?|sumario|table\s+of\s+contents?|contents?)'
    r'\s*:?\s*$',
    re.IGNORECASE,
)

# Entrada del índice: "3.1 Formulación del problema ........ 24".
# Los puntos guía son opcionales; basta el número de página al final.
_RE_ENTRADA_INDICE = re.compile(
    r'^\|?\s*(?P<titulo>[^|]{3,120}?)\s*'
    r'[\s.·…_|]*?(?P<pagina>\d{1,4})\s*\|?\s*$'
)

# Corrida de puntos guía: distingue una línea del índice de un encabezado real.
_RE_PUNTOS_GUIA = re.compile(r'[.·…_]{4,}')

# Entrada bibliográfica APA: "Apellido, I. N. ... (2024)". Sirve para
# confirmar que un encabezado "Referencias" es el de verdad y no una mención
# suelta en el índice, el cuerpo o una celda de tabla.
_RE_ENTRADA_APA = re.compile(
    r"[A-ZÁÉÍÓÚÑÜ][\w'’\-]+(?:\s+[A-ZÁÉÍÓÚÑÜ][\w'’\-]+){0,3}\s*,"
    r"(?:\s*[A-ZÁÉÍÓÚÑ]\.){1,4}[^()]{0,220}?\(\s*(?:19|20)\d{2}[a-z]?\s*\)",
    re.DOTALL,
)
_MIN_ENTRADAS_APA = 3
# Invisibles que el PDF deja incrustados y rompen los patrones de encabezado.
_RE_INVISIBLES_TEXTO = re.compile("[\u200b\u200c\u200d\ufeff\u00ad\u2060]")
_VENTANA_BIBLIOGRAFIA = 4000


_RE_FRAGMENTO_REFERENCIA = re.compile(
    r'\(\s*(?:\d{4}[a-z]?|s\.\s?f\.|n\.\s?d\.)|https?://|doi\.org|\bet\s+al\b'
    r'|[A-ZÁÉÍÓÚÑ]\.\s*[,&]',
    re.IGNORECASE,
)


def _parece_titulo_de_seccion(linea: str) -> bool:
    """
    ¿Este encabezado markdown abre una sección nueva, o es un trozo de una
    referencia bibliográfica que la conversión del PDF formateó como
    encabezado? Un título de sección es corto y no lleva año entre
    paréntesis, DOI, URL ni iniciales de autor. Las letras sueltas («### A»)
    que agrupan la bibliografía por orden alfabético tampoco la cierran.
    """
    texto = re.sub(r'^#{1,3}\s+', '', linea).strip(' *\t')
    if len(texto) <= 2 or len(texto) > 60:
        return False
    return not _RE_FRAGMENTO_REFERENCIA.search(texto)


def _parece_bibliografia(texto_tras_encabezado: str) -> bool:
    """¿Lo que sigue al encabezado son entradas bibliográficas de verdad?"""
    ventana = texto_tras_encabezado[:_VENTANA_BIBLIOGRAFIA]
    return len(_RE_ENTRADA_APA.findall(ventana)) >= _MIN_ENTRADAS_APA

# ── Título de la tesis ───────────────────────────────────────────────────────
# El informe lo necesita como columna, y el nombre del archivo no sirve: suele
# ser un código de repositorio. En la portada el título va siempre entre la
# fórmula del grado ("TESIS PARA OPTAR EL TÍTULO DE...") y la lista de autores.

# Fórmula del grado. Las universidades la escriben de muchas maneras —"TESIS
# PARA OPTAR", "TRABAJO DE SUFICIENCIA PROFESIONAL PARA OPTAR", "INFORME DE
# PRÁCTICAS PARA OBTENER"— así que entre el sustantivo y "para optar" se
# admiten varias palabras en vez de enumerar cada variante.
_RE_FORMULA_GRADO = re.compile(
    r"^.{0,30}?(?:tesis|trabajo|informe|monograf[íi]a|proyecto|memoria)"
    r"(?:\s+[\wáéíóúñ]+){0,4}?\s+para\s+(?:optar|obtener).{0,120}$",
    re.IGNORECASE | re.MULTILINE,
)
_RE_FIN_TITULO = re.compile(
    r"^\s*(?:\*{0,2}\s*)?(?:autor(?:es|a)?|jurado|asesor|l[íi]nea\s+de\s+investigaci[óo]n|"
    r"presentad[oa]\s+por|trujillo|per[úu]|\d{4})\b",
    re.IGNORECASE,
)
# Ruido de portada que nunca forma parte del título.
_RE_LINEA_IGNORABLE = re.compile(
    r"^[\s_\-—=\*#]*$|^(?:universidad|facultad|programa|escuela)\b", re.IGNORECASE
)
# Lo que viene detrás del título en la portada, ya sobre el texto unido.
_RE_COLA_PORTADA = re.compile(
    r"[-_—=]{4,}|\bl[íi]nea\s+de\s+investigaci[óo]n\b|\bautor(?:es|a)?\s*:|"
    r"\bjurado\b|\basesor",
    re.IGNORECASE,
)
# Continuación de la fórmula del grado en una segunda línea: "…PARA OPTAR EL
# TÍTULO" / "PROFESIONAL DE INGENIERO DE COMPUTACIÓN Y SISTEMAS". Se reconoce
# por su contenido y no por su longitud: medir caracteres fallaba en cuanto la
# línea pasaba del umbral y esa continuación acababa tomándose por título.
_RE_CONTINUA_GRADO = re.compile(
    r"^(?:profesional|acad[ée]mico|t[íi]tulo|grado|licenciad[oa]|ingenier[oa]|"
    r"maestr[oa]|magister|mag[íi]ster|doctor|bachiller|sistemas|"
    r"computaci[óo]n|y\s+sistemas)\b|"
    r"\b(?:ingenier[oa]|licenciad[oa]|maestr[oa]|magister|doctor|bachiller)\s+(?:de|en)\b",
    re.IGNORECASE,
)
_MAX_CHARS_PORTADA = 4000


def extraer_titulo_tesis(texto: str) -> str:
    """
    Título de la tesis leído de la portada. Cadena vacía si no se reconoce.
    """
    portada = (texto or "")[:_MAX_CHARS_PORTADA]
    marca = _RE_FORMULA_GRADO.search(portada)
    desde = marca.end() if marca else 0

    partes: list[str] = []
    for linea in portada[desde:].splitlines():
        limpio = re.sub(r"[*#_]+", " ", linea).strip()
        if _RE_FIN_TITULO.match(limpio):
            break
        if _RE_LINEA_IGNORABLE.match(limpio) or len(limpio) < 3:
            # Un separador tras haber acumulado título marca el final.
            if partes and not limpio:
                continue
            continue
        # La fórmula del grado puede continuar en las líneas siguientes.
        if not partes and _RE_CONTINUA_GRADO.match(limpio):
            continue
        partes.append(limpio)
        if sum(len(x) for x in partes) > 260:
            break

    titulo = " ".join(partes)
    # pymupdf4llm a veces pega el separador y la línea siguiente al título, así
    # que el corte se repite sobre el texto ya unido y no solo línea a línea.
    corte = _RE_COLA_PORTADA.search(titulo)
    if corte:
        titulo = titulo[:corte.start()]
    titulo = re.sub(r"\s{2,}", " ", titulo).strip(" .,-–—_")
    return titulo if len(titulo) >= 15 else ""


# ── Detección de citas APA ───────────────────────────────────────────────────
# El detector trabaja sobre el texto APLANADO (app.core.texto.aplanar): el
# markdown de un PDF parte las citas en dos ("(LeCun et\nal., 2015)") y hacer
# que cada regex tolere \n en todos sus huecos nunca alcanza. Al aplanar una
# vez, los patrones quedan legibles y el span se devuelve a coordenadas del
# texto original gracias al mapa posicional.

_MAY = r"[A-ZÁÉÍÓÚÜÑÀÈÌÒÙÂÊÎÔÛÄÖÅÆØÇĆČŠŽŁÐÞ]"
# Palabra capitalizada o inicial suelta ("Westpha", "E.").
_PAL = rf"(?:{_MAY}[\w'’\-]*\.?|{_MAY}\.)"
# Partículas de apellidos compuestos (hispanos, neerlandeses, alemanes...).
_PART = r"(?:de|del|la|las|los|van|von|der|den|di|da|do|dos|el|bin|ibn|Mc|Mac|St\.?)"
# Apellido: hasta 4 palabras capitalizadas → cubre "Hernández Sampieri" y
# autores institucionales como "American Cancer Society".
_NOMBRE = rf"{_PAL}(?:\s+(?:{_PART}\s+)?{_PAL}){{0,3}}"
_ETAL = r"(?:\s*,?\s*et\.?\s+al\.?)"
_CONECT = r"(?:\s*(?:,|;)?\s*(?:y|e|and|&)\s*)"
_AUTORES = rf"{_NOMBRE}(?:(?:,\s*|{_CONECT}){_NOMBRE}){{0,3}}{_ETAL}?"
_ANIO = r"(?:19|20)\d{2}[a-z]?|s\.\s?f\.|n\.\s?d\."
_PAGS = r"(?:\s*,\s*(?:pp?\.|párr\.|cap\.)\s*[\dIVXivx]+(?:\s*[-–]\s*\d+)?)?"

# Una entrada dentro del paréntesis: "Autores, año" (+ página opcional).
_RE_UNIDAD_CITA = re.compile(rf"^\s*(?P<autores>{_AUTORES})\s*,\s*(?P<anio>{_ANIO}){_PAGS}\s*$")
_RE_PARENTESIS = re.compile(r"\(([^()]{3,300}?)\)")
_RE_CONTIENE_ANIO = re.compile(rf"\b(?:{_ANIO})")
_RE_CITA_NARRATIVA = re.compile(
    rf"(?<![\w]){_ETAL}?(?P<autores>{_AUTORES})\s*\(\s*(?P<anio>{_ANIO}){_PAGS}\s*\)"
)
_RE_ETAL_FINAL = re.compile(r"\s*,?\s*et\.?\s+al\.?\s*$", re.IGNORECASE)

# Palabras que delatan prosa en vez de apellido. Si la primera palabra de lo
# capturado está aquí, el match se descarta: evita que un conector de inicio de
# oración se cuele como autor ("Asimismo, Joshi et al (2022)").
_PALABRAS_NO_AUTOR = frozenset("""
el la los las un una unos unas este esta estos estas ese esa aquel aquella
en de del por para con sin segun según desde hasta sobre entre tras durante
the a an in of for and to as at on by is are was were
figura tabla anexo capitulo capítulo seccion sección ecuacion ecuación grafico gráfico
nota fuente elaboracion elaboración propia adaptado tomado recuperado año años
universidad facultad escuela tesis revista vol pp editorial edicion edición
se que lo su sus como mas más ser fue han hay ha fueron sido siendo
asimismo ademas además tambien también sin embargo aunque pero mientras
finalmente igualmente asi así luego entonces posteriormente previamente
actualmente recientemente ahora hoy ayer anteriormente inicialmente
por ejemplo ejemplo caso casos decir esto eso ello cual cuales quien quienes
resultados resultado estudio estudios investigacion investigación analisis análisis
""".split())


# Corte de oración: punto/!/? seguido de espacio y mayúscula de apertura.
_RE_LIMITE_ORACION = re.compile(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡"“\(])')
# Abreviaturas que terminan en punto sin cerrar oración.
_ABREVIATURAS = frozenset("""
et al p pp ej etc fig figs tab vol no nro núm num ed eds trad cap sec
dr dra sr sra ing lic mg ms ph d cf vs aprox inc ltd co corp univ
""".split())


def _es_limite_real(plano: str, pos: int) -> bool:
    """Descarta cortes falsos tras una abreviatura o una inicial ('E.')."""
    anterior = plano[:pos].rstrip()
    if not anterior.endswith("."):
        return True                      # cortó en '!' o '?'
    palabra = re.search(r"([\w'’\-]+)\.$", anterior)
    if not palabra:
        return True
    token = palabra.group(1)
    return len(token) > 1 and token.lower() not in _ABREVIATURAS


# La cita puede ABRIR la oración ("Según (Yusuf et al., 2025), el modelo...").
# Ahí la afirmación va después del paréntesis, no antes.
_RE_CONECTOR_PREVIO = re.compile(
    r"(?:seg[úu]n|de acuerdo con|conforme a|siguiendo a|para|en|como\s+(?:se\s+)?"
    r"(?:señala|indica|sostiene|afirma|menciona|plantea|describe|refiere))\s*[,:]?\s*$",
    re.IGNORECASE,
)
# Sustancia mínima antes del paréntesis para que eso sea la afirmación. Por
# debajo de esto lo previo es un conector y el claim está después de la cita;
# el umbral se mantiene bajo porque extender de más arrastra la oración
# siguiente y el juez terminaría atribuyéndole a la cita algo que no dice.
_MIN_CLAIM_PREVIO = 25


def _oracion_de(plano: str, inicio: int, fin: int, hasta_el_final: bool,
                margen: int = 700) -> str:
    """
    Oración que contiene el span [inicio, fin) del texto aplanado.

    `hasta_el_final=False` (cita parentética) corta en la cita misma: la
    afirmación del tesista es lo que va ANTES del paréntesis. Con True
    (cita narrativa, "Smith (2020) sostiene que...") la afirmación viene
    después, así que se extiende hasta el punto final.
    """
    desde = max(0, inicio - margen)
    inicio_oracion = None
    # Sin endpos: acotar el rango truncaría el lookahead del patrón y se
    # perdería el corte que cae justo antes del span.
    for m in _RE_LIMITE_ORACION.finditer(plano, desde):
        if m.end() > inicio:
            break
        if _es_limite_real(plano, m.start()):
            inicio_oracion = m.end()

    if inicio_oracion is None:
        # Ningún final de oración a la vista: el texto no es prosa corrida
        # (tabla, encabezados, listado). Se toma una ventana corta pegada a la
        # cita en lugar de arrastrar el margen entero, que traía párrafos ajenos.
        recorte = max(0, inicio - 250)
        if recorte == 0:
            inicio_oracion = 0          # la cita cae en la primera oración
        else:
            espacio = plano.find(" ", recorte)
            inicio_oracion = espacio + 1 if 0 <= espacio < inicio else recorte

    if not hasta_el_final:
        previo = plano[inicio_oracion:inicio].strip()
        # "Según (Autor, año), el método mejora X": lo de antes es solo el
        # conector, así que la afirmación está después de la cita.
        if len(previo) < _MIN_CLAIM_PREVIO or _RE_CONECTOR_PREVIO.search(previo):
            hasta_el_final = True
        else:
            return plano[inicio_oracion:fin].strip()

    fin_oracion = min(len(plano), fin + margen)
    for m in _RE_LIMITE_ORACION.finditer(plano, fin, fin_oracion):
        if _es_limite_real(plano, m.start()):
            fin_oracion = m.start()
            break
    return plano[inicio_oracion:fin_oracion].strip()

class CitaDetectada:
    """Una cita localizada en el texto, ya normalizada."""

    __slots__ = ("texto", "original", "tipo", "inicio", "fin", "inicio_plano", "fin_plano")

    def __init__(self, texto: str, original: str, tipo: TipoCita,
                 inicio: int, fin: int, inicio_plano: int = 0, fin_plano: int = 0):
        self.texto = texto          # forma canónica: "(Hernández Sampieri, 2018)"
        self.original = original    # lo que literalmente hay en el PDF (grupo completo)
        self.tipo = tipo
        self.inicio = inicio        # span en el texto ORIGINAL (no aplanado)
        self.fin = fin
        self.inicio_plano = inicio_plano   # mismo span sobre el texto aplanado
        self.fin_plano = fin_plano

    def __repr__(self) -> str:      # pragma: no cover - ayuda en logs
        return f"CitaDetectada({self.texto!r}, {self.tipo.value})"


def _podar_autores(autores: str) -> tuple[str, int] | None:
    """
    Recorta las palabras de prosa que el patrón haya arrastrado por la
    izquierda ("Asimismo, Joshi et al" → "Joshi et al") y valida el resto.
    Devuelve (autores_limpios, desplazamiento_en_caracteres) o None si lo
    capturado no puede ser un apellido ni una institución.
    """
    sin_etal = _RE_ETAL_FINAL.sub("", autores)
    tokens = list(re.finditer(r"[\w'’\-]+", sin_etal))
    if not tokens:
        return None

    # Avanza mientras la palabra inicial sea prosa o no empiece en mayúscula.
    primero = 0
    while primero < len(tokens):
        palabra = tokens[primero].group()
        if palabra[:1].isupper() and palabra.lower() not in _PALABRAS_NO_AUTOR:
            break
        primero += 1
    if primero == len(tokens):
        return None

    # Ninguna palabra restante puede ser prosa: "de los resultados Smith" no vale.
    if any(t.group().lower() in _PALABRAS_NO_AUTOR for t in tokens[primero:]):
        return None

    desplazamiento = tokens[primero].start()
    limpio = autores[desplazamiento:].strip(" ,;")
    if not 2 <= len(limpio) <= 90:
        return None
    return limpio, desplazamiento


def detectar_citas_estructuradas(texto: str, tp=None) -> list[CitaDetectada]:
    """
    Detecta las citas APA del texto y las devuelve normalizadas.

    Un grupo con varias citas —"(Ahnlide et al., 2016; Unlu et al., 2014)"—
    produce UNA CitaDetectada por cada fuente, porque cada una se audita
    contra su propia referencia; todas comparten el mismo `original`, que es
    lo que hay que buscar para localizarlas en el PDF.
    """
    if tp is None:
        tp = aplanar(texto)
    plano = tp.plano
    detectadas: list[CitaDetectada] = []
    consumidos: list[tuple[int, int]] = []

    # 1) Parentéticas, incluidos los grupos separados por ';'
    for m in _RE_PARENTESIS.finditer(plano):
        interior = m.group(1)
        if not _RE_CONTIENE_ANIO.search(interior):
            continue
        unidades = []
        for parte in re.split(r"\s*;\s*", interior):
            um = _RE_UNIDAD_CITA.match(parte.strip())
            podado = _podar_autores(um.group("autores")) if um else None
            if not podado:
                unidades = []
                break
            unidades.append((podado[0], um.group("anio")))
        if not unidades:
            continue
        inicio, fin = tp.a_original(m.start(), m.end())
        original = m.group(0)
        for autores, anio in unidades:
            detectadas.append(CitaDetectada(
                texto=f"({autores}, {anio})",
                original=original,
                tipo=TipoCita.PARENTETICA,
                inicio=inicio, fin=fin,
                inicio_plano=m.start(), fin_plano=m.end(),
            ))
        consumidos.append((m.start(), m.end()))

    # 2) Narrativas que no estén ya dentro de una parentética
    for m in _RE_CITA_NARRATIVA.finditer(plano):
        if any(ini <= m.start() < f for ini, f in consumidos):
            continue
        podado = _podar_autores(m.group("autores"))
        if not podado:
            continue
        autores, desplazamiento = podado
        # El span empieza en el apellido, no en la prosa que se recortó.
        inicio_plano = m.start("autores") + desplazamiento
        inicio, fin = tp.a_original(inicio_plano, m.end())
        detectadas.append(CitaDetectada(
            texto=f"{autores} ({m.group('anio')})",
            original=plano[inicio_plano:m.end()],
            tipo=TipoCita.NARRATIVA,
            inicio=inicio, fin=fin,
            inicio_plano=inicio_plano, fin_plano=m.end(),
        ))

    detectadas.sort(key=lambda c: c.inicio)
    return detectadas


SYSTEM_REFERENCIAS = """Eres un extractor de referencias bibliográficas APA 7ma edición.
Extrae CADA referencia como un objeto JSON separado, uno por línea.
NO uses array. Cada línea debe ser un JSON válido independiente.
Sin texto adicional, sin markdown.
Si falta el año usa null. Si faltan autores usa [].
Marca datos_incompletos como true si falta algún campo clave.

Formato de cada línea:
{"autores": ["Apellido, N."], "anio": 2020, "titulo": "Título", "fuente": "Revista", "doi": "10.xxx o null", "url": "https://... o null", "datos_incompletos": false, "campos_faltantes": []}

El campo "url" es la dirección web de la referencia cuando la entrada la
incluye (páginas de documentación, informes, notas técnicas). Cópiala tal cual,
sin el prefijo "Recuperado de" ni la fecha de consulta. null si no hay."""


class _MatchProxy:
    """Minimal stand-in for re.Match used when detection requires line normalization."""
    def __init__(self, start: int, text: str):
        self._start = start
        self._text = text

    def start(self) -> int:
        return self._start

    def group(self) -> str:
        return self._text


class EntidadExtractionService:

    @staticmethod
    def extraer_titulo_tesis(texto: str) -> str:
        """Título de la portada; cadena vacía si no se reconoce."""
        return extraer_titulo_tesis(texto)

    def extraer_referencias(self, texto: str) -> list[ReferenciaAPA]:
        """
        Extrae SOLO la sección de referencias, no todo el documento.
        """
        texto = self._truncar_antes_de_anexos(texto)
        match = self._encontrar_inicio_referencias(texto)
        if match:
            texto_desde_refs = texto[match.start():]
            fin = self._encontrar_fin_seccion(texto_desde_refs)
            texto_referencias = texto_desde_refs[:fin]
            logger.info("seccion_referencias_encontrada", chars=len(texto_referencias))
        else:
            texto_referencias = texto[-12000:]
            logger.warning("seccion_referencias_no_encontrada_usando_final_del_documento")

        if not texto_referencias.strip():
            return []

        bloques = self._dividir_en_bloques(texto_referencias)
        todas: list[ReferenciaAPA] = []

        for i, bloque in enumerate(bloques):
            logger.info("extrayendo_referencias", bloque=i + 1, total=len(bloques))
            try:
                respuesta = llm_service.completar(
                    system_prompt=SYSTEM_REFERENCIAS,
                    user_prompt=f"Extrae las referencias de este texto:\n\n{bloque}",
                )
                referencias_raw = self._parsear_json(respuesta)
                for ref in referencias_raw:
                    todas.append(ReferenciaAPA(
                        referencia_id=str(uuid.uuid4()),
                        autores=ref.get("autores", []),
                        anio=ref.get("anio"),
                        titulo=ref.get("titulo", "Sin título"),
                        fuente=ref.get("fuente"),
                        doi=ref.get("doi"),
                        url=ref.get("url"),
                        datos_incompletos=ref.get("datos_incompletos", False),
                        campos_faltantes=ref.get("campos_faltantes", []),
                    ))
            except Exception as e:
                logger.error("error_extraccion_referencias", bloque=i, error=str(e))

        todas = self._deduplicar_referencias(todas)
        logger.info("referencias_extraidas", total=len(todas))
        return todas

    @staticmethod
    def _deduplicar_referencias(referencias: list[ReferenciaAPA]) -> list[ReferenciaAPA]:
        """
        Descarta la misma referencia extraída dos veces.

        La sección se procesa en bloques de ~7000 caracteres y una entrada que
        cae en el borde aparece en ambos, así que el LLM la devuelve repetida
        (8 casos en una tesis real). Dos entradas iguales compiten después en
        la vinculación y la marcan como ambigua sin motivo.
        """
        def _clave(ref: ReferenciaAPA) -> tuple:
            titulo = re.sub(r"[^\w]+", " ", (ref.titulo or "").lower()).strip()
            autores = tuple(sorted(
                re.sub(r"[^\w]+", "", a.lower()) for a in (ref.autores or [])
            ))
            return (autores, ref.anio, titulo[:70])

        vistas: dict[tuple, ReferenciaAPA] = {}
        for ref in referencias:
            clave = _clave(ref)
            previa = vistas.get(clave)
            # Ante un duplicado se conserva la más completa (la que trae DOI).
            if previa is None or (not previa.doi and ref.doi):
                vistas[clave] = ref

        descartadas = len(referencias) - len(vistas)
        if descartadas:
            logger.info("referencias_duplicadas_descartadas", descartadas=descartadas,
                        total_crudo=len(referencias))
        return list(vistas.values())

    def extraer_citas(
        self,
        texto: str,
        num_paginas: int,
        toc: list[tuple[int, str]] | None = None,
    ) -> list[CitaEnTexto]:
        """
        Detecta citas APA en el cuerpo del texto (HU-005).

        Excluye la sección de referencias para evitar falsos positivos y, usando
        el índice del documento (TOC embebido o parseado del texto), descarta
        metodología, resultados y conclusiones: las citas relevantes viven en
        introducción, planteamiento del problema, marco de referencias e
        hipótesis/variables. Sin índice utilizable se analiza todo el cuerpo.

        La detección es determinística: el regex sobre texto aplanado es la
        fuente de verdad y de cada span sale la oración que lo contiene. Antes
        la lista la producía el LLM sobre bloques de 7000 caracteres, que
        perdía citas de forma irreproducible y devolvía el texto con los saltos
        de línea del PDF incrustados.
        """
        texto = self._truncar_antes_de_anexos(texto)
        match = self._encontrar_inicio_referencias(texto)

        if match:
            texto_cuerpo = texto[:match.start()]
            logger.info("excluyendo_seccion_referencias", chars_excluidos=len(texto) - match.start())
        else:
            texto_cuerpo = texto

        texto_cuerpo = self._recortar_secciones_no_citables(texto_cuerpo, toc)

        tp = aplanar(texto_cuerpo)
        detectadas = detectar_citas_estructuradas(texto_cuerpo, tp=tp)
        if not detectadas:
            logger.info("no_se_encontraron_citas")
            return []

        total_chars = max(len(tp.plano), 1)
        citas: list[CitaEnTexto] = []

        for det in detectadas:
            es_narrativa = det.tipo == TipoCita.NARRATIVA
            fragmento = _oracion_de(
                tp.plano, det.inicio_plano, det.fin_plano, hasta_el_final=es_narrativa
            )
            if not fragmento:
                fragmento = det.texto

            # Página aproximada por posición relativa; la página real la fija
            # después localizacion_service buscando la cita en el PDF.
            pagina = max(1, min(num_paginas, int(det.inicio_plano / total_chars * num_paginas) + 1))
            limite = 1500 if es_narrativa else 800

            citas.append(CitaEnTexto(
                cita_id=str(uuid.uuid4()),
                texto_cita=det.texto,
                texto_original=det.original,
                tipo=det.tipo,
                pagina=pagina,
                fragmento_oracion=fragmento[:limite],
            ))

        citas = self._deduplicar_citas(citas)

        parenteticas = sum(1 for c in citas if c.tipo == TipoCita.PARENTETICA)
        logger.info(
            "citas_extraidas",
            total=len(citas),
            parenteticas=parenteticas,
            narrativas=len(citas) - parenteticas,
        )
        return citas

    @staticmethod
    def _deduplicar_citas(citas: list[CitaEnTexto]) -> list[CitaEnTexto]:
        """
        Elimina la misma ocurrencia extraída dos veces (típico en límites de
        bloque o cuando el LLM la reporta con más y con menos contexto).

        Dos entradas con el mismo texto_cita cuyos fragmentos se contienen
        mutuamente (normalizando espacios) son la MISMA ocurrencia: se conserva
        la de fragmento más corto, que es la delimitación más precisa de la
        aseveración. La misma cita en párrafos distintos (fragmentos disjuntos)
        se conserva como ocurrencias separadas.
        """
        def _norm(s: str) -> str:
            return re.sub(r'\s+', ' ', s or '').strip().lower()

        dedup: list[CitaEnTexto] = []
        indices_por_cita: dict[str, list[int]] = {}
        descartadas = 0

        for c in citas:
            frag = _norm(c.fragmento_oracion)
            duplicada = False
            for idx in indices_por_cita.get(c.texto_cita, []):
                frag_previo = _norm(dedup[idx].fragmento_oracion)
                mismo_vacio = not frag and not frag_previo
                contenidos = frag and frag_previo and (frag in frag_previo or frag_previo in frag)
                if mismo_vacio or contenidos:
                    if len(frag) < len(frag_previo):
                        dedup[idx] = c  # quedarse con el fragmento más corto
                    duplicada = True
                    descartadas += 1
                    break
            if not duplicada:
                indices_por_cita.setdefault(c.texto_cita, []).append(len(dedup))
                dedup.append(c)

        if descartadas:
            logger.info("citas_duplicadas_descartadas", total=descartadas)
        return dedup

    # ── Filtrado de secciones por índice ─────────────────────────────────────

    def _recortar_secciones_no_citables(
        self, texto: str, toc: list[tuple[int, str]] | None
    ) -> str:
        """
        Elimina del cuerpo las secciones excluidas (metodología, resultados,
        conclusiones) usando el índice como mapa: TOC embebido del PDF si
        existe, si no el índice parseado del propio texto. Una sección
        excluida abarca hasta la siguiente entrada del índice localizada que
        no esté excluida y no sea subsección suya; las entradas protegidas
        (hipótesis/variables) siempre cortan la exclusión. Si no hay índice
        utilizable, el texto se devuelve intacto.
        """
        entradas = list(toc) if toc else self._titulos_desde_indice(texto)
        if not entradas:
            logger.info("indice_no_disponible_se_analiza_todo_el_cuerpo")
            return texto

        localizadas = self._localizar_entradas(texto, entradas)
        if not localizadas:
            logger.warning("titulos_del_indice_no_localizados_en_cuerpo")
            return texto

        spans: list[tuple[int, int]] = []
        i = 0
        while i < len(localizadas):
            pos, nivel, titulo = localizadas[i]
            excluida = _RE_SECCION_EXCLUIDA.search(titulo) and not _RE_SECCION_PROTEGIDA.search(titulo)
            if not excluida:
                i += 1
                continue
            j = i + 1
            while j < len(localizadas):
                pos_j, nivel_j, titulo_j = localizadas[j]
                protegida_j = _RE_SECCION_PROTEGIDA.search(titulo_j)
                excluida_j = _RE_SECCION_EXCLUIDA.search(titulo_j)
                if protegida_j or (not excluida_j and nivel_j <= nivel):
                    break
                j += 1
            fin = localizadas[j][0] if j < len(localizadas) else len(texto)
            spans.append((pos, fin))
            logger.info(
                "seccion_no_citable_descartada",
                seccion=titulo.strip()[:80],
                chars=fin - pos,
            )
            i = j

        if not spans:
            return texto

        partes: list[str] = []
        cursor = 0
        for ini, fin in spans:
            partes.append(texto[cursor:ini])
            cursor = fin
        partes.append(texto[cursor:])
        return "".join(partes)

    def _titulos_desde_indice(self, texto: str) -> list[tuple[int, str]]:
        """
        Parsea el índice/tabla de contenido del propio texto → [(nivel, título)].
        El nivel se infiere de la numeración decimal ("3.1" → 2). Fallback
        para PDFs sin TOC embebido.
        """
        lineas = texto.splitlines()
        inicio = None
        for i, linea in enumerate(lineas):
            linea_norm = re.sub(r'\*+', '', linea).strip()
            if _RE_ENCABEZADO_INDICE.match(linea_norm):
                inicio = i + 1
                break
        if inicio is None:
            return []

        entradas: list[tuple[int, str]] = []
        sin_match = 0
        for linea in lineas[inicio:inicio + 400]:
            plana = re.sub(r'\*+', '', linea).strip()
            if not plana:
                continue
            m = _RE_ENTRADA_INDICE.match(plana)
            if not m:
                sin_match += 1
                # Tolera sub-encabezados intermedios ("ÍNDICE DE TABLAS") y
                # ruido, pero corta cuando el índice claramente terminó.
                if entradas and sin_match > 8:
                    break
                continue
            sin_match = 0
            titulo = m.group('titulo').strip(' .·…_')
            pagina = m.group('pagina')
            # Un año al final sin puntos guía es prosa, no una entrada.
            if re.fullmatch(r'(19|20)\d{2}', pagina) and not _RE_PUNTOS_GUIA.search(plana):
                continue
            if len(titulo) < 3:
                continue
            entradas.append((self._nivel_por_numeracion(titulo), titulo))

        logger.info("indice_textual_parseado", entradas=len(entradas))
        return entradas

    @staticmethod
    def _nivel_por_numeracion(titulo: str) -> int:
        m = re.match(r'^\s*(\d+(?:\.\d+)*)', titulo)
        return m.group(1).count('.') + 1 if m else 1

    @staticmethod
    def _normalizar_titulo_indice(linea: str) -> str:
        linea = re.sub(r'^#{1,3}\s*', '', linea)
        linea = re.sub(r'\*+', ' ', linea)
        return re.sub(r'\s+', ' ', linea).strip().rstrip(':').strip().lower()

    @staticmethod
    def _parece_encabezado_cuerpo(linea: str) -> bool:
        """Línea con pinta de encabezado: markdown, negrita, numerada o mayúsculas."""
        if re.match(r'^(#{1,3}\s|\*{1,2}\S|\d+(\.\d+)*\.?\s)', linea):
            return True
        letras = [c for c in linea if c.isalpha()]
        return bool(letras) and sum(c.isupper() for c in letras) / len(letras) > 0.7

    @classmethod
    def _coincide_titulo(cls, norm_linea: str, norm_titulo: str, es_encabezado: bool) -> bool:
        if norm_linea == norm_titulo:
            return True
        if not es_encabezado:
            return False
        if norm_linea.startswith(norm_titulo):
            resto = norm_linea[len(norm_titulo):].strip()
            # Un número al final delata la línea del propio índice ("título 24").
            return not resto or not resto[-1].isdigit() or norm_titulo[-1].isdigit()
        # El índice a veces trae más texto que el encabezado real del cuerpo.
        return norm_titulo.startswith(norm_linea) and len(norm_linea) >= 8

    def _localizar_entradas(
        self, texto: str, entradas: list[tuple[int, str]]
    ) -> list[tuple[int, int, str]]:
        """
        Posición en el cuerpo del encabezado de cada entrada del índice,
        como [(offset, nivel, título)] ordenado por offset. Las líneas con
        puntos guía (las del propio índice) no cuentan como candidatas.
        """
        candidatas: list[tuple[int, str, bool]] = []
        offset = 0
        for linea in texto.splitlines(keepends=True):
            plana = linea.strip()
            if plana and len(plana) <= 160 and not _RE_PUNTOS_GUIA.search(plana):
                candidatas.append((
                    offset,
                    self._normalizar_titulo_indice(plana),
                    self._parece_encabezado_cuerpo(plana),
                ))
            offset += len(linea)

        localizadas: list[tuple[int, int, str]] = []
        for nivel, titulo in entradas:
            norm_titulo = self._normalizar_titulo_indice(titulo)
            if len(norm_titulo) < 3:
                continue
            matches = [
                off for off, norm, es_enc in candidatas
                if self._coincide_titulo(norm, norm_titulo, es_enc)
            ]
            if matches:
                # La última ocurrencia: el título aparece antes en el índice
                # y su encabezado real viene después en el cuerpo.
                localizadas.append((matches[-1], nivel, titulo))

        localizadas.sort()
        logger.info(
            "entradas_indice_localizadas",
            localizadas=len(localizadas),
            total=len(entradas),
        )
        return localizadas

    @staticmethod
    def _encontrar_inicio_referencias(texto: str):
        """
        Devuelve el re.Match del encabezado de la bibliografía, o None.

        La palabra "referencias" aparece además en el índice, en el cuerpo y
        —lo que rompía la detección— como celda de una tabla que el PDF parte
        en líneas sueltas ("Revisión de" / "referencias"). Quedarse con el
        primer match daba por bibliografía 16 000 caracteres de cuerpo: las
        citas de esa zona se perdían y la extracción de referencias recibía
        texto que no eran referencias.

        Por eso se recogen TODOS los candidatos y se elige, de atrás hacia
        adelante, el primero que vaya seguido de entradas bibliográficas
        reales. El índice y las menciones del cuerpo quedan siempre antes.
        """
        candidatos: dict[int, object] = {}

        for patron in (_RE_REFERENCIAS, _RE_REFERENCIAS_NUMERADA):
            for m in patron.finditer(texto):
                candidatos.setdefault(m.start(), m)

        # Encabezados con negritas partidas ("**7** **REFERENCIAS**") o con
        # invisibles intercalados. Un espacio de ancho cero entre el número y
        # el título —"**7.\u200b** **Referencias bibliográficas**"— basta para
        # que el patrón falle, porque \s no lo reconoce como espacio, y
        # entonces la bibliografía entera queda sin detectar.
        offset = 0
        for linea in texto.splitlines(keepends=True):
            linea_norm = _RE_INVISIBLES_TEXTO.sub(" ", linea)
            linea_norm = re.sub(r'\*+', '', linea_norm).strip()
            if _RE_REFERENCIAS.match(linea_norm) or _RE_REFERENCIAS_NUMERADA.match(linea_norm):
                candidatos.setdefault(offset, _MatchProxy(offset, linea.rstrip('\r\n')))
            offset += len(linea)

        if not candidatos:
            return None

        ordenados = [candidatos[p] for p in sorted(candidatos)]
        for match in reversed(ordenados):
            fin = match.start() + len(match.group())
            if _parece_bibliografia(texto[fin:]):
                logger.debug(
                    "encabezado_referencias_detectado",
                    encabezado=match.group().strip(),
                    posicion=match.start(),
                    candidatos=len(ordenados),
                )
                return match

        # Ninguno va seguido de entradas APA: se toma el último de todos modos,
        # que es el que más probablemente sea la bibliografía.
        logger.warning(
            "encabezado_referencias_sin_entradas_apa",
            candidatos=len(ordenados),
            elegido=ordenados[-1].group().strip(),
        )
        return ordenados[-1]

    @staticmethod
    def _encontrar_fin_seccion(texto_desde_refs: str) -> int:
        """
        Returns the position where the references section ends.
        Looks for the next # heading after the first line (the heading itself).
        If no next heading is found, the section extends to the end of the text.
        """
        # Skip the heading line itself before searching for the next heading
        primera_linea_len = texto_desde_refs.index('\n') + 1 if '\n' in texto_desde_refs else len(texto_desde_refs)
        _RE_SIGUIENTE_SECCION = re.compile(r'^#{1,3}\s+\S', re.MULTILINE)
        for m in _RE_SIGUIENTE_SECCION.finditer(texto_desde_refs, primera_linea_len):
            fin_linea = texto_desde_refs.find('\n', m.start())
            linea = texto_desde_refs[m.start():fin_linea if fin_linea != -1 else len(texto_desde_refs)]
            # La conversión del PDF a veces da formato de encabezado a una
            # entrada de la propia bibliografía («### Alcívar, F. S. (2021)…»,
            # o su continuación tras un salto de página). Cortar ahí dejaba la
            # sección vacía: una tesis entera quedó con 0 referencias.
            if not _parece_titulo_de_seccion(linea):
                continue
            logger.info("fin_seccion_referencias_detectado", posicion=m.start(), encabezado=m.group().strip())
            return m.start()
        return len(texto_desde_refs)

    @staticmethod
    def _truncar_antes_de_anexos(texto: str) -> str:
        """
        Drops everything from the Annexes/Appendix heading onward.
        Must be called before any reference or citation detection.

        Two detection strategies (earliest match wins):
        - _RE_INICIO_ANEXOS: markdown / bold / all-caps heading (no position constraint).
        - _RE_ANEXO_NUMERADO: plain-text "Anexo N." line, only after 70% of the document
          to avoid false positives from in-body references like "como se ve en el Anexo 2".
        """
        posicion = len(texto)

        # Pass 1: markdown / plain bold heading
        m_heading = _RE_INICIO_ANEXOS.search(texto)
        if m_heading:
            posicion = m_heading.start()

        # Pass 2: split-bold headings like "**8** **ANEXOS**" — strip all * and leading
        # numbers from each line, then check against the keyword pattern.
        offset = 0
        for linea in texto.splitlines(keepends=True):
            if offset >= posicion:
                break
            # Los invisibles del PDF ("**8.\u200b** **Anexos**") rompen el
            # patrón: \s no los reconoce como espacio y el encabezado pasa
            # desapercibido, con lo que los anexos se cuelan en el cuerpo.
            linea_norm = _RE_INVISIBLES_TEXTO.sub(" ", linea)
            linea_norm = re.sub(r'\*+', ' ', linea_norm).strip()
            linea_norm = re.sub(r'\s{2,}', ' ', linea_norm)
            if _RE_KEYWORD_ANEXOS.match(linea_norm):
                posicion = min(posicion, offset)
                break
            offset += len(linea)

        # Pass 3: plain-text "Anexo N." line, only after 70% to avoid false positives
        limite_70 = int(len(texto) * 0.70)
        for m_num in _RE_ANEXO_NUMERADO.finditer(texto):
            if m_num.start() >= limite_70:
                posicion = min(posicion, m_num.start())
                break

        if posicion < len(texto):
            logger.info(
                "seccion_anexos_descartada",
                posicion=posicion,
                chars_descartados=len(texto) - posicion,
            )
            return texto[:posicion]
        return texto

    @staticmethod
    def _dividir_en_bloques(texto: str, max_chars: int = 7000) -> list[str]:
        parrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
        logger.info("parrafos_detectados", total=len(parrafos))

        bloques: list[str] = []
        grupo: list[str] = []
        chars_grupo: int = 0

        for parrafo in parrafos:
            if len(parrafo) > max_chars:
                if grupo:
                    bloques.append("\n\n".join(grupo))
                    grupo = []
                    chars_grupo = 0
                partes = _RE_ORACION_FRASE.split(parrafo)
                sub: list[str] = []
                chars_sub = 0
                for parte in partes:
                    costo = (1 if sub else 0) + len(parte)
                    if sub and chars_sub + costo > max_chars:
                        bloques.append(" ".join(sub))
                        sub = [parte]
                        chars_sub = len(parte)
                    else:
                        sub.append(parte)
                        chars_sub += costo
                if sub:
                    bloques.append(" ".join(sub))
            else:
                costo = (2 if grupo else 0) + len(parrafo)
                if grupo and chars_grupo + costo > max_chars:
                    bloques.append("\n\n".join(grupo))
                    grupo = [parrafo]
                    chars_grupo = len(parrafo)
                else:
                    grupo.append(parrafo)
                    chars_grupo += costo

        if grupo:
            bloques.append("\n\n".join(grupo))

        logger.info("bloques_generados", total=len(bloques))
        return [b for b in bloques if b]

    @staticmethod
    def _parsear_json(texto: str) -> list[dict]:
        try:
            limpio = re.sub(r"```json\s*|\s*```", "", texto).strip()

            try:
                resultado = json.loads(limpio)
                return resultado if isinstance(resultado, list) else []
            except json.JSONDecodeError:
                pass

            resultados = []
            for linea in limpio.split('\n'):
                linea = linea.strip()
                if not linea or not linea.startswith('{'):
                    continue
                try:
                    obj = json.loads(linea)
                    if isinstance(obj, dict):
                        resultados.append(obj)
                except json.JSONDecodeError:
                    continue

            if resultados:
                return resultados

            if limpio.startswith('['):
                ultimo_corchete = limpio.rfind('},')
                if ultimo_corchete > 0:
                    try:
                        resultado = json.loads(limpio[:ultimo_corchete + 1] + ']')
                        return resultado if isinstance(resultado, list) else []
                    except json.JSONDecodeError:
                        pass

            return []
        except Exception:
            return []


# Singleton
extraccion_service = EntidadExtractionService()
import json
import re
import uuid
import structlog

from app.services.llm.openai_client import llm_service
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
# Handles single and decimal section numbers (e.g. "3.1. Referencias").
_RE_REFERENCIAS_NUMERADA = re.compile(
    r'^\d+(?:\.\d+)*\.?\s+(?:referencias?(?:\s+\w+)?|bibliograf[íi]a(?:\s+\w+)?)\s*:?\s*$',
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

SYSTEM_REFERENCIAS = """Eres un extractor de referencias bibliográficas APA 7ma edición.
Extrae CADA referencia como un objeto JSON separado, uno por línea.
NO uses array. Cada línea debe ser un JSON válido independiente.
Sin texto adicional, sin markdown.
Si falta el año usa null. Si faltan autores usa [].
Marca datos_incompletos como true si falta algún campo clave.

Formato de cada línea:
{"autores": ["Apellido, N."], "anio": 2020, "titulo": "Título", "fuente": "Revista", "doi": "10.xxx o null", "datos_incompletos": false, "campos_faltantes": []}"""


SYSTEM_CITAS = """Eres un detector de citas APA 7ma edición en texto académico.
Extrae TODAS las citas encontradas en el texto.
Responde ÚNICAMENTE con un JSON array. Sin texto adicional.

Tipos de cita APA:
- Parentética: (Apellido, año) o (Apellido & Apellido, año) o (Apellido et al., año)
- Narrativa: Apellido (año) o Apellido y Apellido (año) o Apellido et al. (año)

Campo "tipo" — regla obligatoria:
- Si el apellido aparece DENTRO del paréntesis → tipo: "PARENTETICA"
  Ejemplo: "(Arévalo et al., 2021)", "(Smith, 2020)", "(Landis y Koch, 1977)"
- Si el apellido está FUERA del paréntesis y solo el año va dentro → tipo: "NARRATIVA"
  Ejemplo: "Arévalo et al. (2021)", "Smith (2020)", "Landis y Koch (1977)", "Walters y Wilder (2023)"
El valor SIEMPRE debe ser exactamente "PARENTETICA" o "NARRATIVA", nunca otro valor.

Para citas narrativas con dos autores como "Landis y Koch (1977)":
- texto_cita: "Landis y Koch (1977)"  ← incluye AMBOS apellidos y el conector "y"
- tipo: "NARRATIVA"

Campo "texto_cita" — reproduce la cita tal como aparece en el texto original:
- Parentética: "(Arévalo et al., 2021)"
- Narrativa: "Arévalo et al. (2021)"

Reglas para fragmento_oracion según tipo de cita:

CITA PARENTÉTICA (Autor, año):
- Identifica a qué idea pertenece esta cita.
- El fragmento empieza donde empieza esa idea: al inicio del párrafo, o justo después del cierre del paréntesis de la cita anterior si hay una.
- El fragmento termina exactamente al cierre del paréntesis de esta cita: ")".
- NO incluyas texto que pertenezca a la siguiente cita.

CITA NARRATIVA Autor (año):
- El fragmento empieza en el nombre del autor, o en el conector previo si lo hay ("Según", "Para", "De acuerdo con", etc.).
- El fragmento termina donde termina la idea atribuida a ese autor (punto seguido, o inicio de otra cita).

CITA COMPUESTA (Autor1, año; Autor2, año):
- Es UNA sola cita. El campo texto_cita debe contener el paréntesis completo con todas las referencias internas.
- El fragmento_oracion es uno solo para todo el paréntesis completo.
- NO la dividas en entradas separadas.
- CORRECTO:   "texto_cita": "(Cheng et al., 2025; Haan, 2025)"  → una entrada, un fragmento
- INCORRECTO: dos entradas separadas, una para Cheng y otra para Haan

Ejemplo con dos citas en el mismo párrafo:

Texto: "El rigor en la precisión semántica es fundamental para la ingeniería. (Arévalo et al., 2021) demuestran que se cometen errores semánticos (Samat et al., 2025)."

CORRECTO para Arévalo:
  texto_cita: "(Arévalo et al., 2021)"
  fragmento_oracion: "El rigor en la precisión semántica es fundamental para la ingeniería. (Arévalo et al., 2021)"

CORRECTO para Samat:
  texto_cita: "(Samat et al., 2025)"
  fragmento_oracion: "demuestran que se cometen errores semánticos (Samat et al., 2025)"

INCORRECTO para Arévalo (NO hagas esto):
  fragmento_oracion: "El rigor en la precisión semántica es fundamental para la ingeniería. (Arévalo et al., 2021) demuestran que se cometen errores semánticos (Samat et al., 2025)"
  razón: el fragmento cruza hacia la siguiente cita

Reglas estrictas:
- NUNCA dejes fragmento_oracion vacío o igual a texto_cita
- NUNCA incluyas en un fragmento texto que pertenezca a la siguiente cita
- El fragmento de una cita parentética SIEMPRE termina en ")" de esa cita
- Si el texto visible comienza a mitad de una idea (empieza con minúscula, conjunción o conector), busca el inicio real de esa idea en el CONTEXTO PREVIO e inclúyelo en fragmento_oracion. En ese caso el fragmento_oracion debe empezar desde el texto del CONTEXTO PREVIO (desde donde empieza la idea) y continuar hasta el cierre de la cita en el bloque principal.
- Si la idea empieza claramente dentro del bloque principal (empieza con mayúscula y sujeto completo), no uses el CONTEXTO PREVIO en fragmento_oracion."""


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
                        datos_incompletos=ref.get("datos_incompletos", False),
                        campos_faltantes=ref.get("campos_faltantes", []),
                    ))
            except Exception as e:
                logger.error("error_extraccion_referencias", bloque=i, error=str(e))

        logger.info("referencias_extraidas", total=len(todas))
        return todas

    def extraer_citas(
        self,
        texto: str,
        num_paginas: int,
        toc: list[tuple[int, str]] | None = None,
    ) -> list[CitaEnTexto]:
        """
        Detecta citas APA en el cuerpo del texto (HU-005).
        Excluye la sección de referencias para evitar falsos positivos y,
        usando el índice del documento (TOC embebido o parseado del texto),
        descarta metodología, resultados y conclusiones: las citas relevantes
        viven en introducción, planteamiento del problema, marco de
        referencias e hipótesis/variables. Sin índice utilizable se analiza
        todo el cuerpo, como antes.
        """
        texto = self._truncar_antes_de_anexos(texto)
        match = self._encontrar_inicio_referencias(texto)

        if match:
            texto_cuerpo = texto[:match.start()]
            logger.info("excluyendo_seccion_referencias", chars_excluidos=len(texto) - match.start())
        else:
            texto_cuerpo = texto

        texto_cuerpo = self._recortar_secciones_no_citables(texto_cuerpo, toc)

        candidatos = self._detectar_citas_regex(texto_cuerpo)
        if not candidatos:
            logger.info("no_se_encontraron_citas_con_regex")
            return []

        bloques = self._dividir_en_bloques(texto_cuerpo)
        todas: list[CitaEnTexto] = []

        for i, bloque in enumerate(bloques):
            pagina_estimada = max(1, int((i / len(bloques)) * num_paginas))
            citas_en_bloque = self._detectar_citas_regex(bloque)
            lista_citas = (
                "\n".join(f"- {c}" for c in sorted(citas_en_bloque))
                if citas_en_bloque
                else "(ninguna detectada)"
            )
            contexto_previo = bloques[i - 1][-600:] if i > 0 else ""
            prefijo_contexto = (
                f"[CONTEXTO PREVIO — solo para determinar dónde empieza una idea"
                f", no extraer citas de aquí]:\n{contexto_previo}\n\n---\n\n"
                if contexto_previo else ""
            )
            try:
                respuesta = llm_service.completar(
                    system_prompt=SYSTEM_CITAS,
                    user_prompt=(
                        f"Detecta las citas APA en este fragmento"
                        f" (página aprox. {pagina_estimada}).\n\n"
                        f"Citas detectadas por regex en este fragmento:\n{lista_citas}\n\n"
                        f"Texto:\n{prefijo_contexto}{bloque}"
                    ),
                )
                citas_raw = self._parsear_json(respuesta)
                for cita in citas_raw:
                    texto_cita = cita.get("texto_cita", "")
                    fragmento = cita.get("fragmento_oracion", "").strip()
                    tipo = (
                        TipoCita.PARENTETICA
                        if cita.get("tipo", "").upper() == "PARENTETICA"
                        else TipoCita.NARRATIVA
                    )
                    if not fragmento or fragmento == texto_cita:
                        if fragmento == texto_cita:
                            logger.warning("fragmento_oracion_igual_a_cita", cita=texto_cita)
                        fragmento = self._extraer_oracion_fallback(bloque, texto_cita)
                    # Para citas parentéticas el fragmento debe terminar en el ")" de la cita.
                    # Si el LLM incluyó texto posterior, se trunca aquí.
                    if tipo == TipoCita.PARENTETICA and texto_cita in fragmento:
                        idx_fin = fragmento.find(texto_cita) + len(texto_cita)
                        fragmento = fragmento[:idx_fin]
                    # Narrativa puede incluir descripciones metodológicas largas (~200-400 palabras).
                    limite = 1500 if tipo == TipoCita.NARRATIVA else 800
                    todas.append(CitaEnTexto(
                        cita_id=str(uuid.uuid4()),
                        texto_cita=texto_cita,
                        tipo=tipo,
                        pagina=cita.get("pagina", pagina_estimada),
                        fragmento_oracion=fragmento[:limite],
                    ))
            except Exception as e:
                logger.error("error_extraccion_citas", bloque=i, error=str(e))

        todas = self._deduplicar_citas(todas)

        logger.info("citas_detectadas_detalle", citas=[c.texto_cita for c in todas])
        logger.info("fragmentos_extraidos", fragmentos=[c.fragmento_oracion[:50] if c.fragmento_oracion else "VACÍO" for c in todas])
        logger.info("citas_extraidas", total=len(todas))
        return todas

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
        Returns the first re.Match for the references section heading, or None.

        Three passes:
        1. _RE_REFERENCIAS against the original text.
        2. _RE_REFERENCIAS_NUMERADA against the original text.
        3. Line-by-line with all bold markers (*) stripped — handles split-bold
           headings like "**7** **REFERENCIAS BIBLIOGRAFICAS**" produced by
           pymupdf4llm when each word has its own bold span.
        """
        match = _RE_REFERENCIAS.search(texto)
        if not match:
            match = _RE_REFERENCIAS_NUMERADA.search(texto)
        if match:
            logger.debug("encabezado_referencias_detectado", encabezado=match.group().strip())
            return match

        # Pass 3: strip inline bold markers per line and retry
        offset = 0
        for linea in texto.splitlines(keepends=True):
            linea_norm = re.sub(r'\*+', '', linea).strip()
            if _RE_REFERENCIAS.match(linea_norm) or _RE_REFERENCIAS_NUMERADA.match(linea_norm):
                logger.debug(
                    "encabezado_referencias_detectado_normalizado",
                    original=linea.strip(),
                    normalizado=linea_norm,
                )
                return _MatchProxy(offset, linea.rstrip('\r\n'))
            offset += len(linea)

        return None

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
        m = _RE_SIGUIENTE_SECCION.search(texto_desde_refs, primera_linea_len)
        if m:
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
            linea_norm = re.sub(r'\*+', ' ', linea).strip()
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

    def _detectar_citas_regex(self, texto: str) -> list[str]:
        patrones = [
            # Parentética simple o con dos autores: (Apellido, año) / (Apellido y Apellido, año)
            r'\([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+y\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?,\s*(?:19|20)\d{2}(?:,\s*p{1,2}\.\s*\d+(?:-\d+)?)?\)',
            # Parentética et al.: (Apellido et al., año)
            r'\([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+et\s+al\.,\s*(?:19|20)\d{2}(?:,\s*p{1,2}\.\s*\d+(?:-\d+)?)?\)',
            # Parentética con &: (Apellido & Apellido, año)
            r'\([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+&\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?,\s*(?:19|20)\d{2}\)',
            # Narrativa simple o et al.: Apellido (año) / Apellido et al. (año)
            r'[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+et\s+al\.)?\s*\((?:19|20)\d{2}\)',
            # Narrativa con dos autores: Apellido y Apellido (año)
            r'[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+y\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s*\((?:19|20)\d{2}\)',
        ]

        encontradas = set()
        for patron in patrones:
            matches = re.findall(patron, texto)
            encontradas.update(matches)

        # Eliminar subcoincidencias: si "Koch (1977)" ya está cubierto por
        # "Landis y Koch (1977)", descartar la versión corta.
        resultado = [
            m for m in encontradas
            if not any(m != otro and m in otro for otro in encontradas)
        ]
        return resultado

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
    def _extraer_oracion_fallback(bloque: str, texto_cita: str) -> str:
        """
        Extrae el fragmento mínimo que rodea a texto_cita cuando el LLM
        devuelve un fragmento vacío o igual a la cita misma.
        Toma hasta 300 chars anteriores a la cita y busca el último punto
        para delimitar el inicio de la oración.
        """
        pos = bloque.find(texto_cita)
        if pos == -1:
            return ""
        inicio = max(0, pos - 300)
        fragmento = bloque[inicio: pos + len(texto_cita)]
        corte = fragmento.rfind(". ", 0, pos - inicio)
        if corte != -1:
            fragmento = fragmento[corte + 2:]
        return fragmento.strip()[:800]

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
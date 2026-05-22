import json
import re
import uuid
import structlog

from app.services.llm.openai_client import llm_service
from app.models.grafo import ReferenciaAPA, CitaEnTexto, TipoCita

logger = structlog.get_logger(__name__)

# Compiled from the original inline patron in extraer_referencias.
# Covers markdown headings, bold, and simple numbered formats.
_RE_REFERENCIAS = re.compile(
    r'^(\*{1,2})?\s*#{0,3}\s*'
    r'(referencias?\s*bibliogr[áa]ficas?|referencias?|bibliograf[íi]a|references?'
    r'|works?\s+cited|fuentes?\s+bibliogr[áa]ficas?|fuentes?\s+de\s+consulta'
    r'|\d+[\.\s]+referencias?|\d+[\.\s]+bibliograf[íi]a)'
    r'\s*(\*{1,2})?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

# Numbered heading with optional word suffix: "7.  Referencias bibliográficas"
_RE_REFERENCIAS_NUMERADA = re.compile(
    r'^\d+\.?\s+(?:referencias?(?:\s+\w+)?|bibliograf[íi]a(?:\s+\w+)?)\s*$',
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

# Matches plain-text numbered annex lines: "Anexo 2." or "Anexo 3:"
# Only used after the 70% position threshold to avoid false positives in the body.
_RE_ANEXO_NUMERADO = re.compile(r'^Anexo\s+\d+[\.\:]', re.MULTILINE)

# Sentence boundary: after .!? followed by whitespace and a Spanish/latin uppercase letter.
_RE_ORACION_FRASE = re.compile(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ])')

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
- Narrativa: Apellido (año)

Campo "tipo" — regla obligatoria:
- Si el apellido aparece DENTRO del paréntesis → tipo: "PARENTETICA"
  Ejemplo: "(Arévalo et al., 2021)", "(Smith, 2020)"
- Si el apellido está FUERA del paréntesis y solo el año va dentro → tipo: "NARRATIVA"
  Ejemplo: "Arévalo et al. (2021)", "Smith (2020)"
El valor SIEMPRE debe ser exactamente "PARENTETICA" o "NARRATIVA", nunca otro valor.

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
- El fragmento de una cita parentética SIEMPRE termina en ")" de esa cita"""


class EntidadExtractionService:

    def extraer_referencias(self, texto: str) -> list[ReferenciaAPA]:
        """
        Extrae SOLO la sección de referencias, no todo el documento.
        """
        texto = self._truncar_antes_de_anexos(texto)
        match = self._encontrar_inicio_referencias(texto)
        if match:
            texto_referencias = texto[match.start():]
            logger.info("seccion_referencias_encontrada", chars=len(texto_referencias))
        else:
            texto_referencias = texto[-4000:]
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

    def extraer_citas(self, texto: str, num_paginas: int) -> list[CitaEnTexto]:
        """
        Detecta citas APA en el cuerpo del texto (HU-005).
        Excluye la sección de referencias para evitar falsos positivos.
        """
        texto = self._truncar_antes_de_anexos(texto)
        match = self._encontrar_inicio_referencias(texto)

        if match:
            texto_cuerpo = texto[:match.start()]
            logger.info("excluyendo_seccion_referencias", chars_excluidos=len(texto) - match.start())
        else:
            texto_cuerpo = texto

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
            try:
                respuesta = llm_service.completar(
                    system_prompt=SYSTEM_CITAS,
                    user_prompt=(
                        f"Detecta las citas APA en este fragmento"
                        f" (página aprox. {pagina_estimada}).\n\n"
                        f"Citas detectadas por regex en este fragmento:\n{lista_citas}\n\n"
                        f"Texto:\n{bloque}"
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
                        fragmento = ""
                    todas.append(CitaEnTexto(
                        cita_id=str(uuid.uuid4()),
                        texto_cita=texto_cita,
                        tipo=tipo,
                        pagina=cita.get("pagina", pagina_estimada),
                        fragmento_oracion=fragmento[:800],
                    ))
            except Exception as e:
                logger.error("error_extraccion_citas", bloque=i, error=str(e))

        vistas: set[tuple] = set()
        dedup: list[CitaEnTexto] = []
        for c in todas:
            key = (c.texto_cita, c.pagina)
            if key not in vistas:
                vistas.add(key)
                dedup.append(c)
        todas = dedup

        logger.info("citas_detectadas_detalle", citas=[c.texto_cita for c in todas])
        logger.info("fragmentos_extraidos", fragmentos=[c.fragmento_oracion[:50] if c.fragmento_oracion else "VACÍO" for c in todas])
        logger.info("citas_extraidas", total=len(todas))
        return todas

    @staticmethod
    def _encontrar_inicio_referencias(texto: str):
        """
        Returns the first re.Match for the references section heading, or None.
        Tries the comprehensive pattern (_RE_REFERENCIAS) first; falls back to
        the numbered-with-suffix pattern (_RE_REFERENCIAS_NUMERADA) to cover
        plain-text headings like "7.  Referencias bibliográficas".
        """
        match = _RE_REFERENCIAS.search(texto)
        if not match:
            match = _RE_REFERENCIAS_NUMERADA.search(texto)
        if match:
            logger.debug("encabezado_referencias_detectado", encabezado=match.group().strip())
        return match

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

        m_heading = _RE_INICIO_ANEXOS.search(texto)
        if m_heading:
            posicion = m_heading.start()

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
            r'\([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+y\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?,\s*(?:19|20)\d{2}(?:,\s*p{1,2}\.\s*\d+(?:-\d+)?)?\)',
            r'\([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+et\s+al\.,\s*(?:19|20)\d{2}(?:,\s*p{1,2}\.\s*\d+(?:-\d+)?)?\)',
            r'\([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+&\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?,\s*(?:19|20)\d{2}\)',
            r'[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+et\s+al\.)?\s*\((?:19|20)\d{2}\)',
        ]

        encontradas = set()
        for patron in patrones:
            matches = re.findall(patron, texto)
            encontradas.update(matches)

        return list(encontradas)

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
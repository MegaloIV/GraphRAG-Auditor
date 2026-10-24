"""
EP-004: Auditoría Semántica y Detección de Alucinaciones

Flujo por cita:
  1. recuperacion_service  → fragmento del paper + nodos del grafo
  2. llm_service           → gpt-5.4-mini decide el veredicto
  3. Neo4j                 → persiste el veredicto en el nodo Cita

Veredictos:
  SUPPORTS → el fragmento respalda directamente la afirmación del tesista
  REFUTES  → el fragmento contradice, niega o es incompatible con la afirmación
  NO_INFO  → no hay fragmento suficiente para determinar si la afirmación es verdadera o falsa
"""
import re
import structlog
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service
from app.services.recuperacion.recuperacion_service import recuperacion_service
from app.services.vectorstore.supabase_service import supabase_vector_service
from app.services.llm.openai_client import llm_service
from app.models.auditoria import (
    VeredictoAuditoria,
    VeredictoTipo,
    AlertaInconsistencia,
    AlertaAlucinacionSistema,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

# Por debajo de esta similitud el fragmento recuperado nunca sostuvo una
# afirmación: medido sobre una tesis real, las 12 citas bajo 0.546 dieron
# NO_INFO sin excepción. Cortar antes del juez ahorra un tercio de las llamadas
# y evita los SUPPORTS por mera coincidencia temática.
_UMBRAL_SIMILITUD = 0.52

# Candidatos que pide la recuperación. Con el umbral filtrando el ruido, un
# pozo más amplio mejora el recall sin ensuciar lo que ve el juez.
_TOP_K_FRAGMENTOS = 8

SYSTEM_AUDITORIA = """Eres un auditor académico especializado en verificación de citas APA 7ma edición.
Tu tarea es determinar si la afirmación del tesista está respaldada, refutada o no verificable
en base al texto real del paper citado.

El tesista puede escribir en español aunque el paper citado esté en inglés.
Evalúa el contenido semántico, no el idioma. Una paráfrasis o traducción fiel cuenta como SUPPORTS.

Recibirás:
- AFIRMACIÓN DEL TESISTA: la oración completa de la tesis que contiene la cita
- CITA APA: la referencia en texto
- FRAGMENTO DEL PAPER: el texto real recuperado del paper citado

Responde ÚNICAMENTE en este formato exacto, en cuatro líneas:
VERDICT: <SUPPORTS|REFUTES|NO_INFO>
EVIDENCE: <la oración EXACTA del FRAGMENTO DEL PAPER que sostiene tu veredicto, copiada carácter por carácter; escribe NINGUNA si el veredicto es NO_INFO>
CONTRADICTION: <SOLO si el veredicto es REFUTES: qué AFIRMA la fuente que no puede ser cierto a la vez que la afirmación del tesista; escribe NINGUNA en los demás casos>
JUSTIFICATION: <una oración que explique el veredicto>

La línea EVIDENCE se coteja automáticamente contra el fragmento. Si transcribes
algo que no está literalmente ahí, el veredicto se descarta. No parafrasees, no
traduzcas, no resumas: copia. Si no puedes copiar una oración del fragmento que
sostenga la afirmación, entonces el veredicto es NO_INFO.

Criterios:
- SUPPORTS: el fragmento contiene evidencia directa que respalda la afirmación del tesista. La información citada es fiel a la fuente.
- REFUTES: el fragmento AFIRMA algo que no puede ser cierto al mismo tiempo que la afirmación del tesista. Hay distorsión, exageración o tergiversación de una afirmación concreta de la fuente.
- NO_INFO: el fragmento no contiene información suficiente para determinar si la afirmación es verdadera o falsa. Incluye citas fabricadas, fuentes inexistentes o fragmentos sin relación con la afirmación.

Reglas estrictas de verificación (obligatorias):
- La dirección importa: verifica que LO QUE AFIRMA EL TESISTA esté contenido en el fragmento. No al revés, y no basta con que "hablen de lo mismo".
- La coincidencia temática NO es evidencia. Si el fragmento trata el mismo tema pero no contiene la afirmación específica del tesista → NO_INFO. Nunca justifiques un SUPPORTS con frases como "coincide con la idea de".
- Si la afirmación incluye cifras, porcentajes o comparaciones concretas ("6.7 veces más rápido", "80% de precisión"), el fragmento debe contener esa cifra o una equivalente inequívoca para dar SUPPORTS.
- No completes con conocimiento propio ni des el beneficio de la duda: solo cuenta lo que está escrito en el fragmento.
- El FRAGMENTO DEL PAPER puede incluir varios pasajes separados por "---": la evidencia puede estar en cualquiera de ellos; evalúa todos antes de decidir.

Regla sobre REFUTES (la más importante):
- Que el fragmento NO diga algo NO es contradecirlo. Si el fragmento simplemente
  no trata la afirmación, no la menciona o no la respalda, el veredicto es
  NO_INFO, jamás REFUTES.
- REFUTES exige poder completar la línea CONTRADICTION con una afirmación
  POSITIVA de la fuente incompatible con la del tesista ("la fuente sostiene
  que el efecto fue de 12%, el tesista dice 45%"). Si para justificarlo
  necesitas escribir "no menciona", "no aparece" o "no respalda", el veredicto
  correcto es NO_INFO.
- Si la oración que transcribes atribuye lo que dice a OTRO autor (por ejemplo
  "Boekaerts argued...", "según Smith (2019)", "[12]"), esa afirmación no es de
  la fuente que estás evaluando y no puede sostener un REFUTES."""


_Q_CITAS_DOCUMENTO = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
OPTIONAL MATCH (c)-[:CITA_A]->(r:Referencia)
CALL (r) {
  OPTIONAL MATCH (r)-[e:ESCRITO_POR]->(a:Autor)
  WITH a ORDER BY e.orden
  RETURN collect(a.nombre) AS autores
}
RETURN
  c.id        AS cita_id,
  c.texto     AS texto_cita,
  c.fragmento AS fragmento_oracion,
  c.pagina    AS pagina,
  r.id        AS ref_id,
  r.titulo    AS ref_titulo,
  r.titulo_oficial AS ref_titulo_oficial,
  r.anio      AS ref_anio,
  r.doi_verificado AS doi_verificado,
  autores
"""

# Citas que aún no tienen veredicto, o cuya fuente se volvió a obtener después de
# haberlas juzgado: son las únicas que hay que auditar en una segunda corrida. El
# resto ya tiene un veredicto emitido sobre exactamente el mismo texto.
_Q_CITAS_PENDIENTES = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
OPTIONAL MATCH (c)-[:CITA_A]->(r:Referencia)
WITH c, r
WHERE c.veredicto IS NULL
   OR c.auditado_en IS NULL
   OR (r.verificado_en IS NOT NULL AND r.verificado_en > c.auditado_en)
CALL (r) {
  OPTIONAL MATCH (r)-[e:ESCRITO_POR]->(a:Autor)
  WITH a ORDER BY e.orden
  RETURN collect(a.nombre) AS autores
}
RETURN
  c.id        AS cita_id,
  c.texto     AS texto_cita,
  c.fragmento AS fragmento_oracion,
  c.pagina    AS pagina,
  r.id        AS ref_id,
  r.titulo    AS ref_titulo,
  r.titulo_oficial AS ref_titulo_oficial,
  r.anio      AS ref_anio,
  r.doi_verificado AS doi_verificado,
  autores
"""

_Q_REFERENCIAS_DOCUMENTO = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
RETURN r.id AS ref_id, r.titulo AS titulo, r.anio AS anio
"""

_Q_REFERENCIAS_CITADAS = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)-[:CITA_A]->(r:Referencia)
RETURN DISTINCT r.id AS ref_id
"""

_Q_GUARDAR_VEREDICTO = """
MATCH (c:Cita {id: $cita_id})
SET c.veredicto              = $veredicto,
    c.justificacion          = $justificacion,
    c.auditado_en            = datetime(),
    c.pagina_paper           = $pagina_paper,
    c.fragmento_evidencia    = $fragmento_evidencia,
    c.fragmento_evidencia_es = $fragmento_evidencia_es,
    c.fragmento_legible      = $fragmento_legible,
    c.cita_textual           = $cita_textual,
    c.clase_cita             = $clase_cita,
    c.seccion_evidencia      = $seccion_evidencia,
    c.contradiccion          = $contradiccion,
    c.similitud              = $similitud
"""

_SYSTEM_LEGIBLE = """Reescribe EN ESPAÑOL el siguiente fragmento de un paper, para que un revisor entienda de un vistazo qué dice.

El fragmento viene de extraer texto de un PDF: llega cortado a media frase, con
restos de tablas, palabras partidas y marcas de maquetación. Tu tarea es
entregarlo en español y legible.

SIEMPRE debes producir texto en español, aunque el original esté en inglés,
portugués o cualquier otro idioma. Devolver el fragmento sin cambios NO es una
respuesta válida salvo que ya estuviera en español y perfectamente legible.

Reglas:
- PROHIBIDO añadir información que no esté en el fragmento: ni contexto, ni
  explicaciones, ni conclusiones, ni datos "que se deducen".
- Conserva EXACTAMENTE cifras, porcentajes, nombres propios y siglas.
- Arregla palabras partidas y quita marcas de maquetación (guiones bajos,
  asteriscos, números de línea, encabezados de revista).
- Si una frase empieza o termina cortada, complétala solo con lo que esté en el
  propio fragmento; si no se puede, márcala con [...] en vez de inventar.
- Si el fragmento no contiene ninguna afirmación inteligible (por ejemplo, es
  solo una portada, un ISSN o una tabla de números), responde exactamente:
  (el fragmento recuperado no contiene texto interpretable)

Responde solo con el texto reescrito, sin comentarios."""

_SYSTEM_TRADUCCION_EVIDENCIA = """Traduce al español académico el siguiente fragmento de un paper.
Conserva EXACTAMENTE las cifras, porcentajes, nombres propios y siglas.
Si el fragmento contiene varios pasajes separados por "---", consérvalos separados igual.
Si el texto ya está en español, devuélvelo sin cambios.
Responde ÚNICAMENTE con la traducción, sin comentarios."""


# ── Citas de procedencia ─────────────────────────────────────────────────────
# "Lista de cotejo adaptada de Soto (2021)" no afirma NADA sobre el contenido
# de Soto: declara de dónde sale un instrumento. Verificarla por contraste de
# texto produce acusaciones falsas —que la versión adaptada tenga otro número
# de ítems es justamente lo que significa "adaptada"— y era el origen de los
# únicos REFUTES emitidos sobre una tesis real.
#
# El patrón mira solo lo que precede INMEDIATAMENTE a la cita: exigirlo pegado
# evita arrastrar afirmaciones legítimas que mencionen un instrumento de paso.
_ARTEFACTOS = (
    r"instrumento|lista\s+de\s+cotejo|r[úu]brica|cuestionario|escala|test|"
    r"inventario|checklist|gu[íi]a|matriz|ficha|encuesta|prueba|formato|plantilla"
)
_ATRIBUCION = (
    r"adaptad[oa]s?|adapted|tomad[oa]s?|basad[oa]s?|propuest[oa]s?|"
    r"elaborad[oa]s?|validad[oa]s?|dise[ñn]ad[oa]s?|creat?ed?|desarrollad[oa]s?"
)
_RE_PROCEDENCIA = re.compile(
    rf"(?:{_ARTEFACTOS})\b[\w\s,]{{0,40}}?"
    rf"(?:{_ATRIBUCION})?\s*\b(?:de|del|por|from|by)\s*$",
    re.IGNORECASE,
)
_VENTANA_PROCEDENCIA = 80


def _es_cita_de_procedencia(fragmento: str, texto_cita: str) -> bool:
    """¿La cita atribuye la autoría de un instrumento en vez de afirmar un hecho?"""
    if not fragmento or not texto_cita:
        return False
    pos = fragmento.find(texto_cita)
    if pos == -1:
        # La cita canónica puede diferir del literal (grupos "A; B"): se
        # reintenta sin los paréntesis.
        pos = fragmento.find(texto_cita.strip("()"))
        if pos == -1:
            return False
    previo = fragmento[max(0, pos - _VENTANA_PROCEDENCIA):pos]
    return bool(_RE_PROCEDENCIA.search(previo))


# ── Guardas del veredicto REFUTES ────────────────────────────────────────────
# Acusar a un tesista de tergiversar una fuente es lo más grave que emite el
# sistema. Sobre tres tesis reales emitió 5 REFUTES y los 5 eran infundados,
# por dos causas distintas que se filtran aquí.

# Causa 1: confundir ausencia con contradicción. El juez escribía "el fragmento
# no menciona X" y dictaminaba REFUTES; por su propia definición eso es NO_INFO.
_RE_AUSENCIA = re.compile(
    r"\bno\s+(?:se\s+)?(?:menciona|mencionan|aparece|aparecen|contiene|contienen|"
    r"respalda|respaldan|indica|indican|trata|figura|dice|dicen|hay|existe|"
    r"proporciona|incluye|incluyen|especifica|detalla)\b"
    r"|\bno\s+est[áa]\b|\bsin\s+informaci[óo]n\b|\bausencia\s+de\b"
    r"|\bnot\s+(?:mentioned|found|stated|present)\b|\bdoes\s+not\s+(?:mention|contain|state)\b",
    re.IGNORECASE,
)

# Causa 2: atribución en cadena. El pasaje transcrito era la fuente citando a un
# TERCERO ("Boekaerts, for example, argued for three types..."), así que esa
# afirmación no es de la fuente evaluada y no puede sostener una acusación.
_RE_ATRIBUCION_TERCERO = re.compile(
    r"\[\d{1,3}(?:[,;\u2013-]\s*\d{1,3})*\]"
    r"|\([A-ZÁÉÍÓÚÑ][\w'’\-]+[^()]{0,60}?,\s*(?:19|20)\d{2}[a-z]?\)"
    r"|\b(?:seg[úu]n|according\s+to|as\s+reported\s+by|as\s+noted\s+by)\b"
    # Entre el apellido y el verbo pueden colarse incisos: "Boekaerts, for
    # example, argued...". Se permiten hasta 30 caracteres sin punto.
    # (?-i:) fuerza mayúscula inicial pese al IGNORECASE global: sin eso
    # "we observed..." —un hallazgo propio— se tomaba por cita a un tercero.
    r"|(?-i:[A-ZÁÉÍÓÚÑ][\w'’\-]+)(?:\s+et\s+al\.?)?[^.]{0,30}?\s"
    r"(?:argued|argues|claimed|claims|reported|reports|found|proposed|proposes|"
    r"suggested|suggests|showed|shows|shown|demonstrated|revealed|indicated|"
    r"noted|observed|established|sostiene|sostuvo|afirma|afirm[óo]|propone|"
    r"propuso|report[óo]|hall[óo]|demostr[óo]|se[ñn]al[óo])\b"
    # Sujetos colectivos que siempre remiten a otros trabajos.
    r"|\b(?:previous|prior|recent|several|other|earlier|various|numerous)\s+"
    r"(?:studies|works?|research|authors|literature|investigations?)\b"
    r"|\b(?:estudios|trabajos|investigaciones|autores)\s+"
    r"(?:previos|anteriores|recientes|varios|otros|diversos)\b",
    re.IGNORECASE,
)

# Sujetos que se refieren al PROPIO paper: "The results showed...", "our study
# found..." son hallazgos de la fuente, no de un tercero, y no deben marcarse.
_RE_SUJETO_PROPIO = re.compile(
    r"\b(?:the|our|this|these|los|las|el|la|nuestr[oa]s?|est[eoa]s?)\s+"
    r"(?:present\s+|current\s+|),?\s*"
    r"(?:results?|findings?|study|studies|paper|work|research|analysis|data|"
    r"experiments?|resultados?|hallazgos?|estudio|trabajo|investigaci[óo]n|an[áa]lisis)\b",
    re.IGNORECASE,
)

# Un pasaje de esta sección es, por definición, la fuente resumiendo trabajo
# ajeno: la evidencia no es un hallazgo suyo.
SECCION_TRABAJOS_RELACIONADOS = "trabajos_relacionados"

_NINGUNA = {"", "ninguna", "none", "n/a", "na", "-"}


def _evidencia_es_de_tercero(cita: str) -> bool:
    """
    ¿El pasaje transcrito atribuye lo que dice a otro trabajo?

    Un match del patrón cuyo sujeto es el propio paper ("The results showed")
    no cuenta: es un hallazgo de la fuente, no una cita a terceros.
    """
    if not cita or not _RE_ATRIBUCION_TERCERO.search(cita):
        return False
    # Marcadores explícitos de cita mandan sobre cualquier sujeto.
    if re.search(r"\[\d{1,3}", cita) or re.search(
        r"\([A-ZÁÉÍÓÚÑ][\w'’\-]+[^()]{0,60}?,?\s*(?:19|20)\d{2}", cita
    ):
        return True
    return not _RE_SUJETO_PROPIO.search(cita)


def _refutacion_es_sostenible(
    contradiccion: str, justificacion: str, cita: str
) -> tuple[bool, str]:
    """
    ¿El REFUTES se sostiene? Devuelve (es_sostenible, motivo_del_rechazo).

    Se exige una afirmación positiva e incompatible de la fuente. Declarar que
    el fragmento "no dice" algo describe una ausencia, y una ausencia nunca
    contradice.
    """
    if (contradiccion or "").strip().lower() in _NINGUNA:
        return False, "no declaró qué afirma la fuente que sea incompatible"

    for campo, texto in (("la contradicción", contradiccion), ("la justificación", justificacion)):
        if _RE_AUSENCIA.search(texto or ""):
            return False, f"{campo} describe una ausencia, no una contradicción"

    if _evidencia_es_de_tercero(cita or ""):
        return False, "el pasaje citado atribuye lo que dice a un tercero, no a la fuente"

    return True, ""


_RE_NO_ALFANUM = re.compile(r"[^\w\s]", re.UNICODE)
# Palabras seguidas que deben coincidir para dar por buena la transcripción.
# Menos es demasiado laxo (cualquier frase hecha coincide); más castiga
# variaciones inocuas de puntuación o guiones del PDF.
_PALABRAS_COTEJO = 8
# Una transcripción de dos o tres palabras coincide con cualquier cosa y no
# demuestra nada: se exige al menos una frase.
_MIN_PALABRAS_CITA = 5


def _normalizar_cotejo(texto: str) -> list[str]:
    return _RE_NO_ALFANUM.sub(" ", (texto or "").lower()).split()


def _cita_esta_en(cita_textual: str, fragmento: str) -> bool:
    """
    ¿La frase que el juez dice haber copiado está realmente en el fragmento?

    El cotejo es por palabras, ignorando puntuación y espaciado, porque el
    markdown del PDF mete guiones y saltos que el modelo normaliza al copiar.
    Basta con que coincida una tirada de palabras suficientemente larga.
    """
    palabras_cita = _normalizar_cotejo(cita_textual)
    if len(palabras_cita) < _MIN_PALABRAS_CITA:
        return False
    palabras_frag = _normalizar_cotejo(fragmento)
    if not palabras_frag:
        return False

    n = min(_PALABRAS_COTEJO, len(palabras_cita))
    frag = " ".join(palabras_frag)
    for inicio in range(len(palabras_cita) - n + 1):
        if " ".join(palabras_cita[inicio:inicio + n]) in frag:
            return True
    return False


class AuditoriaService:

    # ── HU-010: Auditar todas las citas del documento ────────────────────

    def auditar_documento(self, documento_id: str,
                          solo_pendientes: bool = False) -> list[VeredictoAuditoria]:
        """
        Itera las citas del documento, recupera evidencia y emite un veredicto
        con gpt-5.4-mini por cada una. Persiste el veredicto en Neo4j.

        Con `solo_pendientes` se auditan únicamente las citas sin veredicto y
        aquellas cuya fuente se volvió a obtener después de la primera corrida.
        Es lo que se necesita al reauditar tras aportar fuentes nuevas: volver a
        juzgar las que ya tienen veredicto sobre el mismo texto cuesta una
        llamada al modelo por cita y devuelve el mismo resultado.
        """
        consulta = _Q_CITAS_PENDIENTES if solo_pendientes else _Q_CITAS_DOCUMENTO
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            citas_raw = list(session.run(consulta, doc_id=documento_id))

        if not citas_raw:
            logger.info("auditoria_sin_citas", doc_id=documento_id,
                        solo_pendientes=solo_pendientes)
            return []

        veredictos = []
        total = len(citas_raw)

        logger.info("auditoria_paralela_iniciada", doc_id=documento_id, total=total,
                    solo_pendientes=solo_pendientes, max_workers=5)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    self._auditar_cita,
                    documento_id=documento_id,
                    cita_id=registro["cita_id"],
                    texto_cita=registro["texto_cita"] or "",
                    fragmento_oracion=registro["fragmento_oracion"] or "",
                    pagina=registro["pagina"] or 0,
                    ref_id=registro["ref_id"],
                    ref_titulo=registro["ref_titulo_oficial"] or registro["ref_titulo"] or "",
                    ref_anio=registro["ref_anio"],
                    autores=registro["autores"] or [],
                    doi_verificado=registro["doi_verificado"],
                ): registro
                for registro in citas_raw
            }
            for future in as_completed(futures):
                try:
                    veredicto = future.result()
                    veredictos.append(veredicto)
                except Exception as e:
                    logger.error("error_auditando_cita", error=str(e))

        logger.info(
            "auditoria_completada",
            doc_id=documento_id,
            total=total,
            supports=sum(1 for v in veredictos if v.veredicto == VeredictoTipo.SUPPORTS),
            refutes=sum(1 for v in veredictos if v.veredicto == VeredictoTipo.REFUTES),
            no_info=sum(1 for v in veredictos if v.veredicto == VeredictoTipo.NO_INFO),
        )
        return veredictos

    # ── HU-011: Alertas de inconsistencias estructurales ────────────────

    def detectar_inconsistencias(self, documento_id: str) -> dict:
        """
        Detecta:
        - Citas sin referencia bibliográfica (no tienen CITA_A)
        - Referencias listadas que nunca se citan en el cuerpo
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            citas_raw = list(session.run(_Q_CITAS_DOCUMENTO, doc_id=documento_id))
            refs_raw  = list(session.run(_Q_REFERENCIAS_DOCUMENTO, doc_id=documento_id))
            refs_citadas_raw = list(session.run(_Q_REFERENCIAS_CITADAS, doc_id=documento_id))

        ids_refs_citadas = {r["ref_id"] for r in refs_citadas_raw}

        # Citas sin referencia vinculada
        citas_sin_ref = [
            AlertaInconsistencia(
                tipo="cita_sin_referencia",
                descripcion="La cita no tiene una referencia bibliográfica correspondiente en la lista de referencias.",
                elemento=r["texto_cita"] or "",
                ubicacion=f"Página {r['pagina'] or '?'}",
            )
            for r in citas_raw
            if not r["ref_id"]
        ]

        # Referencias sin citar en el cuerpo
        refs_sin_citar = [
            AlertaInconsistencia(
                tipo="referencia_sin_citar",
                descripcion="La referencia está listada en la bibliografía pero nunca se cita en el cuerpo del texto.",
                elemento=r["titulo"] or "",
                ubicacion="Sección de referencias",
            )
            for r in refs_raw
            if r["ref_id"] not in ids_refs_citadas
        ]

        return {
            "citas_sin_referencia": citas_sin_ref,
            "referencias_sin_citar": refs_sin_citar,
        }

    # ── HU-012: Alertas de alucinaciones del sistema ────────────────────

    def detectar_alucinaciones_sistema(
        self,
        veredictos: list[VeredictoAuditoria],
    ) -> list[AlertaAlucinacionSistema]:
        """
        Filtra los veredictos NO_INFO y los convierte en alertas
        explícitas para que el revisor no confíe en esos resultados.
        """
        return [
            AlertaAlucinacionSistema(
                cita_id=v.cita_id,
                texto_cita=v.texto_cita,
                pagina=v.pagina,
                razon_no_verificable=v.justificacion,
            )
            for v in veredictos
            if v.veredicto == VeredictoTipo.NO_INFO
            # Una cita de procedencia no es verificable por diseño: marcarla
            # como alucinación del sistema sería un falso aviso.
            and v.metodo_recuperacion != "procedencia"
        ]

    # ── Internos ─────────────────────────────────────────────────────────

    def _auditar_cita(
        self,
        documento_id: str,
        cita_id: str,
        texto_cita: str,
        fragmento_oracion: str = "",
        pagina: int = 0,
        ref_id: str | None = None,
        ref_titulo: str = "",
        ref_anio: int | None = None,
        autores: list[str] = [],
        doi_verificado: str | None = None,
    ) -> VeredictoAuditoria:
        if not ref_id:
            veredicto = VeredictoAuditoria(
                cita_id=cita_id,
                texto_cita=texto_cita,
                pagina=pagina,
                veredicto=VeredictoTipo.NO_INFO,
                justificacion="No se encontró una referencia bibliográfica vinculada a esta cita.",
                metodo_recuperacion="no_encontrado",
                pagina_paper=None,
            )
            self._persistir_veredicto(veredicto, pagina_paper=None)
            return veredicto

        if _es_cita_de_procedencia(fragmento_oracion, texto_cita):
            logger.debug("cita_de_procedencia", cita_id=cita_id, cita=texto_cita)
            veredicto = VeredictoAuditoria(
                cita_id=cita_id,
                texto_cita=texto_cita,
                fragmento_oracion=fragmento_oracion,
                pagina=pagina,
                veredicto=VeredictoTipo.NO_INFO,
                justificacion=(
                    "La cita atribuye la autoría de un instrumento o método, no afirma "
                    "un hecho sobre el contenido de la fuente: no se verifica por "
                    "contraste de texto. Revisa a mano que la adaptación esté declarada."
                ),
                referencia_id=ref_id,
                titulo_referencia=ref_titulo,
                anio_referencia=ref_anio,
                autores_referencia=autores,
                metodo_recuperacion="procedencia",
                pagina_paper=None,
            )
            self._persistir_veredicto(veredicto, pagina_paper=None, clase="procedencia")
            return veredicto

        resultado = recuperacion_service.consultar_cita(
            documento_id=documento_id,
            cita_id=cita_id,
            n_fragmentos=_TOP_K_FRAGMENTOS,
            fragmento_oracion=fragmento_oracion,
        )

        pagina_paper = resultado.pagina_paper

        logger.debug(
            "cita_auditada_chunk",
            cita_id=cita_id,
            pagina_paper=pagina_paper,
            chunk_index=resultado.chunk_index,
            similitud=resultado.similitud,
        )

        if resultado.metodo in ("no_encontrado", "solo_grafo") or not resultado.fragmento_relevante:
            veredicto = VeredictoAuditoria(
                cita_id=cita_id,
                texto_cita=texto_cita,
                pagina=pagina,
                veredicto=VeredictoTipo.NO_INFO,
                justificacion=(
                    "No se encontró texto del paper citado en la base de conocimiento. "
                    "La referencia existe en el grafo pero no hay fragmento disponible para verificar."
                ),
                referencia_id=ref_id,
                titulo_referencia=ref_titulo,
                anio_referencia=ref_anio,
                doi_referencia=resultado.doi_referencia,
                autores_referencia=autores,
                metodo_recuperacion=resultado.metodo,
                pagina_paper=pagina_paper,
            )
            self._persistir_veredicto(veredicto, pagina_paper=pagina_paper)
            return veredicto

        # Evidencia hallada en un paper VECINO, no en el citado. Se somete al
        # juez igual —hay que comprobar que el pasaje sostenga la afirmación—
        # pero el resultado nunca puede ser SUPPORTS: no verifica la cita, la
        # contradice en su atribución. Se emite como NO_INFO con una
        # explicación que sí es accionable.
        if resultado.metodo == "vecino_grafo":
            tipo_vecino, _, cita_vecino, _ = self._llamar_llm(
                texto_cita=texto_cita,
                fragmento_oracion=fragmento_oracion,
                fragmento=resultado.fragmento_relevante,
                seccion=resultado.seccion_evidencia,
            )

            if tipo_vecino == VeredictoTipo.SUPPORTS:
                otra = resultado.titulo_evidencia or "otra fuente de tu bibliografía"
                motivo = {
                    "citada_por_la_fuente": "es una de las fuentes que el paper que citas cita a su vez",
                    "autor_compartido": "comparte autoría con el paper que citas",
                    "concepto_compartido": "trata el mismo concepto",
                }.get(resultado.via_grafo, "está en tu bibliografía")
                justificacion = (
                    f"La afirmación NO aparece en la fuente que citas, pero sí en "
                    f"«{otra[:90]}», que {motivo}. Revisa si confundiste la referencia."
                )
                clase = "evidencia_en_otra_fuente"
            else:
                justificacion = (
                    "No se encontró respaldo en la fuente citada ni en las fuentes "
                    "vecinas de tu bibliografía."
                )
                clase = "afirmacion"

            veredicto = VeredictoAuditoria(
                cita_id=cita_id, texto_cita=texto_cita,
                fragmento_oracion=fragmento_oracion, pagina=pagina,
                veredicto=VeredictoTipo.NO_INFO, justificacion=justificacion,
                fragmento_evidencia=resultado.fragmento_relevante,
                cita_textual=cita_vecino or "",
                seccion_evidencia=resultado.seccion_evidencia,
                similitud=resultado.similitud,
                referencia_id=ref_id, titulo_referencia=ref_titulo,
                anio_referencia=ref_anio, autores_referencia=autores,
                metodo_recuperacion=resultado.metodo,
                pagina_paper=resultado.pagina_paper,
            )
            self._persistir_veredicto(
                veredicto, pagina_paper=resultado.pagina_paper, clase=clase
            )
            return veredicto

        # Las anclas léxicas (cifras del claim halladas por término exacto)
        # rescatan casos donde el vector no llega, así que no se les aplica
        # el umbral.
        if resultado.similitud < _UMBRAL_SIMILITUD and resultado.anclas_lexicas == 0:
            logger.debug(
                "cita_bajo_umbral", cita_id=cita_id, similitud=resultado.similitud
            )
            veredicto = VeredictoAuditoria(
                cita_id=cita_id,
                texto_cita=texto_cita,
                fragmento_oracion=fragmento_oracion,
                pagina=pagina,
                veredicto=VeredictoTipo.NO_INFO,
                justificacion=(
                    "El texto más parecido del paper citado queda lejos de la afirmación "
                    f"(similitud {resultado.similitud:.2f}): no hay pasaje que la sostenga."
                ),
                fragmento_evidencia=resultado.fragmento_relevante,
                similitud=resultado.similitud,
                referencia_id=ref_id,
                titulo_referencia=ref_titulo,
                anio_referencia=ref_anio,
                doi_referencia=resultado.doi_referencia,
                autores_referencia=autores,
                metodo_recuperacion=resultado.metodo,
                pagina_paper=pagina_paper,
            )
            self._persistir_veredicto(veredicto, pagina_paper=pagina_paper)
            return veredicto

        veredicto_tipo, justificacion, cita_textual, contradiccion = self._llamar_llm(
            texto_cita=texto_cita,
            fragmento_oracion=fragmento_oracion,
            fragmento=resultado.fragmento_relevante,
            seccion=resultado.seccion_evidencia,
        )

        if veredicto_tipo == VeredictoTipo.SUPPORTS and (
            resultado.seccion_evidencia == SECCION_TRABAJOS_RELACIONADOS
            or _evidencia_es_de_tercero(cita_textual or "")
        ):
            justificacion = self._anotar_atribucion(
                documento_id, ref_id, cita_textual, justificacion,
                seccion=resultado.seccion_evidencia,
            )

        veredicto = VeredictoAuditoria(
            cita_id=cita_id,
            texto_cita=texto_cita,
            fragmento_oracion=fragmento_oracion,
            pagina=pagina,
            veredicto=veredicto_tipo,
            justificacion=justificacion,
            fragmento_evidencia=resultado.fragmento_relevante,
            fragmento_evidencia_es=self._traducir_evidencia(resultado.fragmento_relevante),
            fragmento_legible=self._redactar_legible(resultado.fragmento_relevante),
            cita_textual=cita_textual,
            seccion_evidencia=resultado.seccion_evidencia,
            contradiccion=contradiccion,
            similitud=resultado.similitud,
            referencia_id=ref_id,
            titulo_referencia=ref_titulo,
            anio_referencia=ref_anio,
            doi_referencia=resultado.doi_referencia,
            autores_referencia=autores,
            metodo_recuperacion=resultado.metodo,
            pagina_paper=pagina_paper,
        )
        self._persistir_veredicto(veredicto, pagina_paper=pagina_paper)
        return veredicto

    @staticmethod
    def _anotar_atribucion(
        documento_id: str, ref_id: str | None, cita_textual: str,
        justificacion: str, seccion: str = "",
    ) -> str:
        """
        Añade al veredicto de quién es realmente la afirmación.

        El pasaje que la respalda es la fuente citando a otro, así que se
        resuelve el marcador contra la bibliografía del paper citado (grafo) y
        se dice si esa fuente primaria está o no en la de la tesis. Si no se
        puede resolver —la editorial no depositó su bibliografía, o el marcador
        es ambiguo— queda la advertencia genérica, que ya es útil.
        """
        from app.services.grafo.atribucion_service import atribucion_service

        primaria = atribucion_service.resolver(documento_id, ref_id or "", cita_textual)
        if not primaria:
            logger.info("evidencia_atribuida_a_tercero", seccion=seccion or "?",
                        cita=cita_textual[:80])
            return (
                f"{justificacion} [ATENCIÓN: el pasaje que respalda la afirmación es la "
                "fuente citando a terceros, no un hallazgo propio. Comprueba que "
                "corresponda al autor citado.]"
            )

        quien = " ".join(x for x in [
            primaria["autor"], f"({primaria['anio']})" if primaria["anio"] else ""
        ] if x) or (primaria["titulo"][:60] or "otra fuente")
        logger.info(
            "atribucion_en_cadena_resuelta", fuente_primaria=quien,
            en_bibliografia=primaria["en_bibliografia"], cita=cita_textual[:60],
        )
        if primaria["en_bibliografia"]:
            aviso = (
                f"[ATRIBUCIÓN INDIRECTA: la afirmación no es un hallazgo de la fuente "
                f"citada; la atribuye a {quien}, que sí está en tus referencias. "
                f"Cítala a ella.]"
            )
        else:
            aviso = (
                f"[ATRIBUCIÓN INDIRECTA: la afirmación no es un hallazgo de la fuente "
                f"citada; la atribuye a {quien}, que NO está en tus referencias. "
                f"Deberías citar y añadir la fuente primaria.]"
            )
        return f"{justificacion} {aviso}"

    @staticmethod
    def _redactar_legible(fragmento: str) -> str:
        """
        Versión legible del fragmento para el informe.

        El texto que sale del PDF llega troceado y con ruido; esta versión lo
        ordena SIN agregar nada. Es la columna que permite a un revisor
        entender de un vistazo qué dice el paper, sin leer el pasaje crudo.
        """
        if not fragmento or not fragmento.strip():
            return ""
        try:
            return llm_service.completar(
                system_prompt=_SYSTEM_LEGIBLE,
                user_prompt=fragmento[:4000],
            ).strip()
        except Exception as e:
            logger.warning("redaccion_legible_fallida", error=str(e))
            return ""

    @staticmethod
    def _traducir_evidencia(fragmento: str) -> str:
        """
        Traduce el fragmento de evidencia al español para mostrarlo en las
        cartillas y el Excel. El original en inglés se persiste igual (juez,
        trazabilidad y módulo de coherencia usan el original). Si la
        traducción falla, se devuelve vacío y la UI cae al original.
        """
        if not fragmento or not fragmento.strip():
            return ""
        try:
            return llm_service.completar(
                system_prompt=_SYSTEM_TRADUCCION_EVIDENCIA,
                user_prompt=fragmento[:4000],
            ).strip()
        except Exception as e:
            logger.warning("traduccion_evidencia_fallida", error=str(e))
            return ""

    def _llamar_llm(
        self, texto_cita: str, fragmento_oracion: str, fragmento: str,
        seccion: str = "",
    ) -> tuple[VeredictoTipo, str, str, str]:
        """
        Llama a gpt-5.4-mini y parsea el veredicto.
        Si el LLM falla o responde de forma inesperada → NO_VERIFICABLE.
        """
        afirmacion = fragmento_oracion if fragmento_oracion else texto_cita
        prompt = (
            f"AFIRMACIÓN DEL TESISTA: {afirmacion}\n\n"
            f"CITA APA: {texto_cita}\n\n"
            f"FRAGMENTO DEL PAPER: {fragmento}"
        )

        try:
            respuesta = llm_service.completar(
                system_prompt=SYSTEM_AUDITORIA,
                user_prompt=prompt,
            )

            veredicto_tipo, justificacion, cita_textual, contradiccion = (
                self._parsear_respuesta_llm(respuesta)
            )

            # Un REFUTES debe DEMOSTRAR la incompatibilidad, no declararla.
            if veredicto_tipo == VeredictoTipo.REFUTES:
                sostenible, motivo = _refutacion_es_sostenible(
                    contradiccion, justificacion, cita_textual
                )
                if not sostenible:
                    logger.warning("refutacion_degradada", motivo=motivo, cita=cita_textual[:100])
                    return (
                        VeredictoTipo.NO_INFO,
                        f"El fragmento no permite afirmar que la fuente contradiga la cita: "
                        f"{motivo}. Queda sin verificar.",
                        cita_textual,
                        "",
                    )

            # El juez debe copiar del fragmento, no describirlo. Si lo que
            # transcribió no está ahí, se lo inventó: se vio dar SUPPORTS
            # citando una frase ("mejoró la experiencia de los estudiantes")
            # que no aparecía en la evidencia recuperada.
            if veredicto_tipo != VeredictoTipo.NO_INFO and not _cita_esta_en(cita_textual, fragmento):
                logger.warning(
                    "veredicto_descartado_cita_inventada",
                    veredicto=veredicto_tipo.value,
                    cita_textual=cita_textual[:120],
                )
                return (
                    VeredictoTipo.NO_INFO,
                    "El verificador no pudo respaldar su veredicto con una frase literal "
                    "del paper, así que la afirmación queda sin confirmar.",
                    "", "",
                )

            return veredicto_tipo, justificacion, cita_textual, contradiccion

        except Exception as e:
            logger.error("error_llm_auditoria", error=str(e))
            return (
                VeredictoTipo.NO_INFO,
                "El servicio de verificación no pudo procesar esta cita.",
                "", "",
            )

    @staticmethod
    def _parsear_respuesta_llm(respuesta: str) -> tuple[VeredictoTipo, str, str, str]:
        """
        Parsea la respuesta del LLM con el formato:
        VERDICT: SUPPORTS|REFUTES|NO_INFO
        EVIDENCE: <transcripción literal del fragmento>
        CONTRADICTION: <qué afirma la fuente que es incompatible; solo REFUTES>
        JUSTIFICATION: ...
        """
        lineas = respuesta.strip().splitlines()
        veredicto_tipo = VeredictoTipo.NO_INFO
        justificacion = "Could not interpret the verifier's response."
        cita_textual = ""
        contradiccion = ""

        for linea in lineas:
            linea_upper = linea.strip().upper()
            if linea_upper.startswith("VERDICT:"):
                valor = linea.split(":", 1)[1].strip().upper()
                if "SUPPORTS" in valor:
                    veredicto_tipo = VeredictoTipo.SUPPORTS
                elif "REFUTES" in valor:
                    veredicto_tipo = VeredictoTipo.REFUTES
                elif "NO_INFO" in valor or "NO INFO" in valor:
                    veredicto_tipo = VeredictoTipo.NO_INFO

            elif linea_upper.startswith("EVIDENCE:"):
                cita_textual = linea.split(":", 1)[1].strip()

            elif linea_upper.startswith("CONTRADICTION:"):
                contradiccion = linea.split(":", 1)[1].strip()

            elif linea_upper.startswith("JUSTIFICATION:"):
                justificacion = linea.split(":", 1)[1].strip()

        return veredicto_tipo, justificacion, cita_textual, contradiccion

    def _persistir_veredicto(
        self,
        veredicto: VeredictoAuditoria,
        pagina_paper: Optional[int] = None,
        clase: str = "afirmacion",
    ) -> None:
        """Guarda el veredicto en el nodo Cita de Neo4j."""
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                session.run(
                    _Q_GUARDAR_VEREDICTO,
                    cita_id=veredicto.cita_id,
                    veredicto=veredicto.veredicto.value,
                    justificacion=veredicto.justificacion,
                    pagina_paper=pagina_paper,
                    fragmento_evidencia=veredicto.fragmento_evidencia or "",
                    fragmento_evidencia_es=veredicto.fragmento_evidencia_es or "",
                    fragmento_legible=veredicto.fragmento_legible or "",
                    cita_textual=veredicto.cita_textual or "",
                    clase_cita=clase,
                    seccion_evidencia=veredicto.seccion_evidencia or "",
                    contradiccion=veredicto.contradiccion or "",
                    similitud=float(veredicto.similitud or 0.0),
                )
        except Exception as e:
            logger.error("error_persistiendo_veredicto", cita_id=veredicto.cita_id, error=str(e))

    # ── EP-RAGAS: Evaluación post-auditoría con RAGAS ──────────────────────

    def evaluar_ragas_documento(self, doc_id: str) -> dict:
        from app.services.evaluacion.ragas_service import ragas_service

        # 1. Leer citas auditadas con fragmento de paper disponible
        _Q_LEER_CITAS = """
            MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
            WHERE c.fragmento IS NOT NULL
              AND c.fragmento_evidencia IS NOT NULL
              AND c.fragmento_evidencia <> ''
              AND c.veredicto IS NOT NULL
              AND c.justificacion IS NOT NULL
            RETURN
                c.id                  AS cita_id,
                c.fragmento           AS fragmento_oracion,
                c.veredicto           AS veredicto,
                c.justificacion       AS justificacion,
                c.fragmento_evidencia AS fragmento_evidencia
        """
        with neo4j_service.driver.session(
            database=settings.neo4j_database
        ) as session:
            resultado = session.run(_Q_LEER_CITAS, doc_id=doc_id)
            citas = [dict(r) for r in resultado]

        if not citas:
            logger.info("ragas_sin_citas_evaluables", doc_id=doc_id)
            return {"total_evaluadas": 0}

        logger.info("ragas_iniciando", doc_id=doc_id, total=len(citas))

        # 2. Evaluar con RAGAS (Neo4j cerrado)
        scores_por_cita = []
        for i, cita in enumerate(citas, 1):
            logger.info("ragas_evaluando_cita", numero=i, total=len(citas))
            # pregunta = el claim del tesista que se está verificando
            # respuesta = veredicto + justificación del auditor (respuesta completa del sistema)
            respuesta_auditoria = f"{cita['veredicto']}: {cita['justificacion']}"
            scores = ragas_service.evaluar_cita(
                pregunta  = cita["fragmento_oracion"],
                respuesta = respuesta_auditoria,
                contextos = [cita["fragmento_evidencia"]],
            )
            scores_por_cita.append({
                "cita_id": cita["cita_id"],
                **scores
            })

        # 3. Guardar en Neo4j en batch (solo las 3 métricas válidas)
        _Q_GUARDAR_RAGAS = """
            MATCH (c:Cita {id: $cita_id})
            SET c.faithfulness      = $faithfulness,
                c.answer_relevancy  = $answer_relevancy,
                c.context_precision = $context_precision,
                c.ragas_evaluado_en = datetime()
        """
        with neo4j_service.driver.session(
            database=settings.neo4j_database
        ) as session:
            for scores in scores_por_cita:
                try:
                    session.run(_Q_GUARDAR_RAGAS, scores)
                except Exception as e:
                    logger.error(
                        "ragas_error_guardando",
                        cita_id=scores["cita_id"],
                        error=str(e)
                    )

        def promedio(key):
            vals = [s[key] for s in scores_por_cita if s.get(key) is not None]
            return round(sum(vals) / len(vals), 3) if vals else None

        return {
            "total_evaluadas":            len(scores_por_cita),
            "faithfulness_promedio":      promedio("faithfulness"),
            "answer_relevancy_promedio":  promedio("answer_relevancy"),
            "context_precision_promedio": promedio("context_precision"),
        }


# Singleton
auditoria_service = AuditoriaService()
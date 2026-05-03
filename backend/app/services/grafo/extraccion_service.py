import json
import re
import uuid
import structlog

from app.services.llm.openai_client import llm_service
from app.models.grafo import ReferenciaAPA, CitaEnTexto, TipoCita

logger = structlog.get_logger(__name__)

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
- Parentética: (Apellido, año) o (Apellido & Apellido, año)
- Narrativa: Apellido (año)

Formato de cada objeto:
{
  "texto_cita": "(García, 2020)",
  "tipo": "parentetica",
  "pagina": 5,
  "fragmento_oracion": "texto de contexto donde aparece la cita"
}"""


class EntidadExtractionService:

    def extraer_referencias(self, texto: str) -> list[ReferenciaAPA]:
        """
        Extrae SOLO la sección de referencias, no todo el documento.
        """
        patron = r'(?i)^(\*{1,2})?\s*#{0,3}\s*(referencias?\s*bibliogr[áa]ficas?|referencias?|bibliograf[íi]a|references?|works?\s+cited|fuentes?\s+bibliogr[áa]ficas?|fuentes?\s+de\s+consulta|\d+[\.\s]+referencias?|\d+[\.\s]+bibliograf[íi]a)\s*(\*{1,2})?\s*$'
        match = re.search(patron, texto, re.MULTILINE)
        if match:
            texto_referencias = texto[match.start():]
            logger.info("seccion_referencias_encontrada", chars=len(texto_referencias))
        else:
            texto_referencias = texto[-4000:]
            logger.warning("seccion_referencias_no_encontrada_usando_final_del_documento")

        if not texto_referencias.strip():
            return []

        bloques = self._dividir_texto(texto_referencias, max_chars=8000)
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
        # Excluir sección de referencias del texto a analizar
        patron_refs = r'(?i)^(\*{1,2})?\s*#{0,3}\s*(referencias?\s*bibliogr[áa]ficas?|referencias?|bibliograf[íi]a|references?)\s*(\*{1,2})?\s*$'
        match = re.search(patron_refs, texto, re.MULTILINE)

        if match:
            texto_cuerpo = texto[:match.start()]
            logger.info("excluyendo_seccion_referencias", chars_excluidos=len(texto) - match.start())
        else:
            texto_cuerpo = texto

        candidatos = self._detectar_citas_regex(texto_cuerpo)
        if not candidatos:
            logger.info("no_se_encontraron_citas_con_regex")
            return []

        bloques = self._dividir_texto(texto_cuerpo, max_chars=8000)
        todas: list[CitaEnTexto] = []

        for i, bloque in enumerate(bloques):
            pagina_estimada = max(1, int((i / len(bloques)) * num_paginas))
            try:
                respuesta = llm_service.completar(
                    system_prompt=SYSTEM_CITAS,
                    user_prompt=f"Detecta las citas APA en este fragmento (página aprox. {pagina_estimada}):\n\n{bloque}",
                )
                citas_raw = self._parsear_json(respuesta)
                for cita in citas_raw:
                    tipo = (
                        TipoCita.PARENTETICA
                        if cita.get("tipo") == "parentetica"
                        else TipoCita.NARRATIVA
                    )
                    todas.append(CitaEnTexto(
                        cita_id=str(uuid.uuid4()),
                        texto_cita=cita.get("texto_cita", ""),
                        tipo=tipo,
                        pagina=cita.get("pagina", pagina_estimada),
                        fragmento_oracion=cita.get("fragmento_oracion", "")[:200],
                    ))
            except Exception as e:
                logger.error("error_extraccion_citas", bloque=i, error=str(e))

        logger.info("citas_detectadas_detalle", citas=[c.texto_cita for c in todas])
        logger.info("citas_extraidas", total=len(todas))
        return todas

    def _detectar_citas_regex(self, texto: str) -> list[str]:
        """
        Detección estricta de citas APA reales.
        Solo acepta patrones con apellido(s) + año válido.
        """
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
    def _dividir_texto(texto: str, max_chars: int) -> list[str]:
        if len(texto) <= max_chars:
            return [texto]

        bloques = []
        inicio = 0
        while inicio < len(texto):
            fin = inicio + max_chars
            if fin < len(texto):
                corte = texto.rfind("\n\n", inicio, fin)
                if corte == -1:
                    corte = texto.rfind("\n", inicio, fin)
                if corte == -1:
                    corte = fin
                else:
                    corte += 1
            else:
                corte = len(texto)
            bloques.append(texto[inicio:corte].strip())
            inicio = corte

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
import json
import re
import uuid
import structlog

from app.services.llm.groq_client import llm_service
from app.models.grafo import ReferenciaAPA, CitaEnTexto, TipoCita

logger = structlog.get_logger(__name__)

SYSTEM_REFERENCIAS = """Eres un extractor de referencias bibliográficas APA 7ma edición.
Analiza el texto y extrae cada referencia de forma estructurada.
Responde ÚNICAMENTE con un JSON array. Sin texto adicional, sin markdown, sin explicaciones.

Formato de cada objeto:
{
  "autores": ["Apellido, N."],
  "anio": 2020,
  "titulo": "Título del trabajo",
  "fuente": "Nombre de la revista o editorial",
  "doi": "10.xxxx/xxxxx o null",
  "datos_incompletos": false,
  "campos_faltantes": []
}

Si falta el año usa null. Si faltan autores usa []. 
Marca datos_incompletos como true si falta algún campo clave."""

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
        Reduce las llamadas al LLM de 14 a 1 o 2 máximo.
        """
        # Buscar donde empieza la sección de referencias
        patron = r'(?i)^#{1,3}\s*(referencias?|bibliograf[íi]a|references?|works?\s+cited)\s*$'
        match = re.search(patron, texto, re.MULTILINE)

        if match:
            texto_referencias = texto[match.start():]
            logger.info("seccion_referencias_encontrada", chars=len(texto_referencias))
        else:
            # Si no encuentra la sección, usar los últimos 4000 chars
            texto_referencias = texto[-4000:]
            logger.warning("seccion_referencias_no_encontrada_usando_final_del_documento")

        if not texto_referencias.strip():
            return []

        bloques = self._dividir_texto(texto_referencias, max_chars=6000)
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
        Usa regex primero para validar que hay citas antes de llamar a Gemini.
        """
        candidatos = self._detectar_citas_regex(texto)
        if not candidatos:
            logger.info("no_se_encontraron_citas_con_regex")
            return []

        bloques = self._dividir_texto(texto, max_chars=4000)
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

        logger.info("citas_extraidas", total=len(todas))
        return todas

    def _detectar_citas_regex(self, texto: str) -> list[str]:
        """
        Detección rápida con regex antes de llamar al LLM.
        Evita gastar tokens si el documento no tiene citas APA.
        """
        patron_parentetica = r'\([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+et\s+al\.)?(?:\s*[,&]\s*[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?,\s*\d{4}(?:,\s*p{1,2}\.\s*\d+(?:-\d+)?)?\)'
        patron_narrativa = r'[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+et\s+al\.)?\s*\(\d{4}\)'

        encontradas = re.findall(patron_parentetica, texto)
        encontradas += re.findall(patron_narrativa, texto)
        return list(set(encontradas))

    @staticmethod
    def _dividir_texto(texto: str, max_chars: int) -> list[str]:
        """
        Divide texto en bloques respetando saltos de párrafo.
        Evita cortar una referencia o cita a la mitad.
        """
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
        """
        Parsea la respuesta JSON de Gemini de forma segura.
        Gemini a veces envuelve el JSON en bloques de markdown.
        """
        try:
            limpio = re.sub(r"```json\s*|\s*```", "", texto).strip()
            resultado = json.loads(limpio)
            return resultado if isinstance(resultado, list) else []
        except json.JSONDecodeError as e:
            logger.warning("json_parse_error", error=str(e))
            return []


# Singleton
extraccion_service = EntidadExtractionService()
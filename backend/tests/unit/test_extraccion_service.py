"""
Tests unitarios para el servicio de extracción de entidades.
Cubre HU-004 y HU-005 con casos de regex y parsing JSON.
No llama al LLM real — los tests de integración lo mockean.
"""
import re
from pathlib import Path

import pytest
import app.services.grafo.extraccion_service as extraccion_mod
from app.services.grafo.extraccion_service import (
    EntidadExtractionService,
    SYSTEM_CITAS,
    _RE_INICIO_ANEXOS,
    _RE_ANEXO_NUMERADO,
    _RE_REFERENCIAS_NUMERADA,
)

_PDF_TESIS = Path(__file__).parent.parent / "Informe de tesis_AlcaldeLavadoMatias.pdf"


@pytest.fixture
def service():
    return EntidadExtractionService()


class TestDetectarCitasRegex:
    """HU-005: Detección de citas parentéticas y narrativas."""

    def test_detecta_cita_parentetica_simple(self, service):
        texto = "Este fenómeno fue descrito por primera vez (García, 2020)."
        citas = service._detectar_citas_regex(texto)
        assert any("García" in c for c in citas)

    def test_detecta_cita_narrativa(self, service):
        texto = "Como señala López (2019), el modelo propuesto es válido."
        citas = service._detectar_citas_regex(texto)
        assert any("López" in c for c in citas)

    def test_detecta_multiples_citas(self, service):
        texto = (
            "Según Martínez (2021), el enfoque es válido. "
            "Sin embargo (Rodríguez, 2022) propone una alternativa."
        )
        citas = service._detectar_citas_regex(texto)
        assert len(citas) >= 2

    def test_no_detecta_version_como_cita(self, service):
        texto = "La versión (3.0) del software fue lanzada en 2020."
        citas = service._detectar_citas_regex(texto)
        assert len(citas) == 0

    def test_no_detecta_año_suelto_como_cita(self, service):
        texto = "El año 2020 fue complicado para todos."
        citas = service._detectar_citas_regex(texto)
        assert len(citas) == 0

    def test_detecta_cita_con_pagina(self, service):
        texto = "El autor lo expresa claramente (García, 2020, p. 45)."
        citas = service._detectar_citas_regex(texto)
        assert any("García" in c for c in citas)


class TestDividirTexto:
    """Validación del batching de texto."""

    def test_texto_corto_no_se_divide(self, service):
        texto = "Texto corto de prueba."
        bloques = service._dividir_texto(texto, max_chars=1000)
        assert len(bloques) == 1

    def test_texto_largo_se_divide(self, service):
        texto = "Párrafo de prueba.\n\n" * 100
        bloques = service._dividir_texto(texto, max_chars=500)
        assert len(bloques) > 1

    def test_bloques_no_estan_vacios(self, service):
        texto = "Párrafo de prueba.\n\n" * 50
        bloques = service._dividir_texto(texto, max_chars=500)
        for bloque in bloques:
            assert len(bloque.strip()) > 0


class TestParsearJson:
    """Validación del parser JSON robusto."""

    def test_parsea_json_limpio(self, service):
        json_str = '[{"titulo": "Paper de prueba", "autores": ["García, J."]}]'
        resultado = service._parsear_json(json_str)
        assert len(resultado) == 1
        assert resultado[0]["titulo"] == "Paper de prueba"

    def test_parsea_json_con_fence_markdown(self, service):
        json_str = '```json\n[{"titulo": "Paper"}]\n```'
        resultado = service._parsear_json(json_str)
        assert len(resultado) == 1

    def test_retorna_lista_vacia_ante_json_invalido(self, service):
        resultado = service._parsear_json("esto no es JSON para nada")
        assert resultado == []

    def test_retorna_lista_vacia_ante_texto_vacio(self, service):
        resultado = service._parsear_json("")
        assert resultado == []


class TestClasificacionTipoCita:
    """Garantiza que el tipo se determina por texto_cita, ignorando lo que devuelva el LLM."""

    def _mock_completar(self, tipo_llm: str):
        """Devuelve un mock que reporta siempre el tipo_llm indicado."""
        def completar_mock(system_prompt: str, user_prompt: str, **kwargs) -> str:
            return (
                f'[{{"texto_cita": "(Arévalo et al., 2021)", "tipo": "{tipo_llm}",'
                f' "pagina": 1, "fragmento_oracion": "Texto (Arévalo et al., 2021)."}},'
                f' {{"texto_cita": "García (2020)", "tipo": "{tipo_llm}",'
                f' "pagina": 1, "fragmento_oracion": "García (2020) propone un modelo."}}]'
            )
        return completar_mock

    def test_parentetica_cuando_texto_empieza_con_parentesis(self, monkeypatch, service):
        monkeypatch.setattr(
            extraccion_mod.llm_service, "completar",
            self._mock_completar("narrativa"),  # LLM dice NARRATIVA, debe corregirse
        )
        citas = service.extraer_citas("Texto (Arévalo et al., 2021).", num_paginas=1)
        parentetica = next(c for c in citas if c.texto_cita.startswith("("))
        assert parentetica.tipo.value == "parentetica"

    def test_narrativa_cuando_texto_no_empieza_con_parentesis(self, monkeypatch, service):
        monkeypatch.setattr(
            extraccion_mod.llm_service, "completar",
            self._mock_completar("parentetica"),  # LLM dice PARENTETICA, debe corregirse
        )
        citas = service.extraer_citas("García (2020) propone un modelo.", num_paginas=1)
        narrativa = next(c for c in citas if not c.texto_cita.startswith("("))
        assert narrativa.tipo.value == "narrativa"


class TestExtraerCitasSystemPrompt:
    """Integración: verifica que extraer_citas pasa el system prompt correcto al LLM."""

    def test_system_prompt_contiene_palabras_clave(self):
        assert "empieza donde empieza esa idea" in SYSTEM_CITAS
        assert "termina exactamente al cierre" in SYSTEM_CITAS
        assert "NO la dividas" in SYSTEM_CITAS

    def test_llm_recibe_system_prompt_correcto(self, monkeypatch, service):
        llamadas: list[str] = []

        def completar_mock(system_prompt: str, user_prompt: str, **kwargs) -> str:
            llamadas.append(system_prompt)
            return (
                '[{"texto_cita": "(García, 2020)", "tipo": "parentetica",'
                ' "pagina": 1, "fragmento_oracion": "Texto de prueba (García, 2020)"}]'
            )

        monkeypatch.setattr(extraccion_mod.llm_service, "completar", completar_mock)

        service.extraer_citas("Texto de prueba (García, 2020).", num_paginas=1)

        assert len(llamadas) >= 1, "El LLM no fue llamado"
        system_usado = llamadas[0]
        assert "empieza donde empieza esa idea" in system_usado
        assert "termina exactamente al cierre" in system_usado
        assert "NO la dividas" in system_usado


class TestTruncarAnexos:
    """Pre-filtrado de sección de Anexos antes de la extracción."""

    def test_trunca_h1_anexos(self, service):
        texto = "Cuerpo del documento.\n\n# Anexos\n\nContenido de anexo que no debe extraerse."
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Contenido de anexo" not in resultado
        assert "Cuerpo del documento" in resultado

    def test_trunca_h2_anexo(self, service):
        texto = "Cuerpo.\n\n## Anexo\n\nContenido contaminante."
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Contenido contaminante" not in resultado

    def test_trunca_h3_apendices(self, service):
        texto = "Cuerpo.\n\n### Apéndices\n\nTablas extras."
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Tablas extras" not in resultado

    def test_trunca_negrita_anexos(self, service):
        texto = "Cuerpo.\n\n**Anexos**\n\nContenido."
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Contenido" not in resultado

    def test_trunca_mayusculas_anexos(self, service):
        texto = "Cuerpo.\n\nANEXOS\n\nContenido."
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Contenido" not in resultado

    def test_trunca_apendice_singular(self, service):
        texto = "Cuerpo.\n\n# Apéndice\n\nDatos extra."
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Datos extra" not in resultado

    def test_trunca_appendix_ingles(self, service):
        texto = "Body text.\n\n# Appendix\n\nExtra content."
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Extra content" not in resultado

    def test_sin_anexos_devuelve_texto_intacto(self, service):
        texto = "# Introducción\n\nContenido.\n\n# Referencias\n\n- Autor (2020)."
        resultado = service._truncar_antes_de_anexos(texto)
        assert resultado == texto

    def test_descarta_todo_lo_que_sigue_a_los_anexos(self, service):
        texto = (
            "Conclusiones.\n\n"
            "# Anexos\n\n"
            "## Anexo A\n\nTabla de datos.\n\n"
            "## Anexo B\n\nCódigo fuente."
        )
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Anexo A" not in resultado
        assert "Anexo B" not in resultado
        assert "Conclusiones" in resultado

    def test_referencias_antes_de_anexos_se_preservan(self, service):
        texto = (
            "# Introducción\n\nTexto.\n\n"
            "# Referencias\n\n- García, J. (2020). Título.\n\n"
            "# Anexos\n\nDatos que no deben incluirse."
        )
        resultado = service._truncar_antes_de_anexos(texto)
        assert "García, J. (2020)" in resultado
        assert "Datos que no deben incluirse" not in resultado

    def test_trunca_anexo_numerado_despues_del_70_porciento(self, service):
        # "Anexo 2." aparece en la posición 1002, que es > 70% de ~1060 chars totales.
        cuerpo = "x" * 1000
        anexo = "\n\nAnexo 2. Evidencias de la investigación.\n\nContenido que debe descartarse."
        texto = cuerpo + anexo
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Contenido que debe descartarse" not in resultado
        assert len(resultado) < len(texto)

    def test_no_trunca_anexo_numerado_antes_del_70_porciento(self, service):
        # "Anexo 2." está en la posición 0 (< 70%) → no debe truncar.
        prefijo = "Anexo 2. Evidencias de la investigación.\n\n"
        sufijo = "x" * 1000
        texto = prefijo + sufijo
        resultado = service._truncar_antes_de_anexos(texto)
        assert resultado == texto

    def test_formato_exacto_de_la_tesis(self, service):
        # Simula el formato real: líneas planas tipo "Anexo N. Título." al final.
        cuerpo = "Conclusiones del trabajo de investigación.\n\n" + "x" * 900
        lineas_anexos = (
            "\nAnexo 2. Evidencias de la ejecución de la investigación."
            "\nAnexo 3. Resolución de aprobación del proyecto."
        )
        texto = cuerpo + lineas_anexos
        resultado = service._truncar_antes_de_anexos(texto)
        assert "Evidencias de la ejecución" not in resultado
        assert "Resolución de aprobación" not in resultado
        assert "Conclusiones del trabajo" in resultado


class TestDetectarInicioReferencias:
    """Detección del encabezado de la sección de referencias."""

    def test_detecta_encabezado_numerado_con_adjetivo(self, service):
        # Caso real: "7.  Referencias bibliográficas" como texto plano numerado.
        texto = (
            "...cuerpo...\n\n"
            "7.  Referencias bibliográficas\n\n"
            "Apellido, N. (2024). Título del artículo.\n\n"
            "Anexo 1. Algo"
        )
        match = service._encontrar_inicio_referencias(texto)

        assert match is not None, "El encabezado '7.  Referencias bibliográficas' no fue detectado"
        assert "Referencias" in match.group()

        # La sección va desde el encabezado hasta antes del inicio del anexo
        pos_anexo = texto.find("\nAnexo 1.")
        texto_refs = texto[match.start():pos_anexo]
        assert "Apellido, N. (2024). Título del artículo." in texto_refs

    def test_detecta_encabezado_markdown_clasico(self, service):
        texto = "# Introducción\n\nTexto.\n\n# Referencias\n\n- Autor (2020)."
        match = service._encontrar_inicio_referencias(texto)
        assert match is not None
        assert "Referencias" in match.group()

    def test_detecta_bibliografia_numerada_sin_adjetivo(self, service):
        texto = "Cuerpo.\n\n8. Bibliografía\n\nAutor (2021). Título."
        match = service._encontrar_inicio_referencias(texto)
        assert match is not None
        assert "Bibliografía" in match.group()

    def test_detecta_referencias_numeradas_sin_adjetivo(self, service):
        texto = "Cuerpo.\n\n5. Referencias\n\nAutor (2020)."
        match = service._encontrar_inicio_referencias(texto)
        assert match is not None

    def test_retorna_none_si_no_hay_seccion_referencias(self, service):
        texto = "# Introducción\n\nContenido sin sección de referencias."
        match = service._encontrar_inicio_referencias(texto)
        assert match is None

    def test_patron_numerado_no_captura_dentro_del_cuerpo(self, service):
        # Una mención inline no debe confundirse con el encabezado de sección.
        # El patrón requiere que la línea comience con el número y termine ahí.
        texto = "Como se describe en las referencias 3.1 del documento, el modelo es válido."
        match = service._encontrar_inicio_referencias(texto)
        assert match is None


@pytest.mark.skipif(
    not _PDF_TESIS.exists(),
    reason="PDF de tesis no disponible en el entorno de prueba",
)
class TestTruncarAnexosConPDFReal:
    """Verifica que la detección de anexos funciona sobre el PDF de tesis real."""

    def test_trunca_seccion_anexos_en_tesis_real(self, service):
        import pymupdf4llm

        texto = pymupdf4llm.to_markdown(str(_PDF_TESIS))
        texto_truncado = service._truncar_antes_de_anexos(texto)

        assert len(texto_truncado) < len(texto), (
            "Se esperaba encontrar una sección de Anexos en la tesis y descartar "
            "el contenido posterior"
        )
        # El encabezado markdown de anexos no debe aparecer en el texto truncado
        assert not _RE_INICIO_ANEXOS.search(texto_truncado), (
            "El encabezado markdown de Anexos/Apéndice sigue presente tras el truncado"
        )
        # El patrón numerado no debe aparecer después del 70% del texto truncado
        limite_70 = int(len(texto_truncado) * 0.70)
        for m in _RE_ANEXO_NUMERADO.finditer(texto_truncado):
            if m.start() >= limite_70:
                pytest.fail(
                    f"Patrón 'Anexo N.' encontrado en posición {m.start()} "
                    f"(después del umbral 70% = {limite_70}) del texto truncado"
                )
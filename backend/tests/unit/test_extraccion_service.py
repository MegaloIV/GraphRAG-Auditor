"""
Tests unitarios para el servicio de extracción de entidades.
Cubre HU-004 y HU-005 con casos de regex y parsing JSON.
No llama a Groq — solo prueba lógica interna.
"""
import pytest
from app.services.grafo.extraccion_service import EntidadExtractionService


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
"""
Tests unitarios para la propagación de pagina_paper en RecuperacionService.
No conecta a Neo4j ni ChromaDB reales.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services.recuperacion.recuperacion_service import (
    RecuperacionService,
    ResultadoRecuperacion,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fragmento_chroma(pagina: int, chunk_index: int, similitud: float = 0.92) -> dict:
    return {
        "texto": f"Contenido de la página {pagina}.",
        "metadata": {
            "doi": "10.1234/test",
            "doi_normalizado": "10_1234_test",
            "titulo": "Paper de prueba",
            "anio": "2024",
            "nivel_confianza": "abstract",
            "referencia_id": "ref_1",
            "pagina": pagina,
            "chunk_index": chunk_index,
        },
        "similitud": similitud,
    }


def _ruta_neo4j(cita_id: str = "cita_1") -> dict:
    return {
        "cita_id": cita_id,
        "texto_cita": "Smith (2020) afirma que X.",
        "ref_id": "ref_1",
        "doi": "10.1234/test",
        "doi_raw": None,
        "ref_titulo": "Paper de prueba",
        "ref_anio": 2020,
        "autores": ["Smith, J."],
        "confianza_vinculo": 0.9,
    }


# ---------------------------------------------------------------------------
# Tests de propagación de pagina_paper
# ---------------------------------------------------------------------------

class TestPaginaPaperPropagacion:

    def _service_con_mocks(self, fragmentos: list[dict], ruta: dict | None):
        """Construye un RecuperacionService con Neo4j y ChromaDB mockeados."""
        service = RecuperacionService.__new__(RecuperacionService)

        neo4j_mock = MagicMock()
        session_mock = MagicMock()
        session_mock.__enter__ = MagicMock(return_value=session_mock)
        session_mock.__exit__ = MagicMock(return_value=False)
        result_mock = MagicMock()
        result_mock.single.return_value = ruta
        session_mock.run.return_value = result_mock
        neo4j_mock.driver.session.return_value = session_mock

        embedding_mock = MagicMock()
        embedding_mock.buscar_similares.return_value = fragmentos

        with (
            patch("app.services.recuperacion.recuperacion_service.neo4j_service", neo4j_mock),
            patch("app.services.recuperacion.recuperacion_service.embedding_service", embedding_mock),
        ):
            resultado = service.consultar_cita(
                documento_id="doc_1",
                cita_id="cita_1",
            )

        return resultado

    def test_pagina_paper_llega_como_3(self):
        fragmentos = [_fragmento_chroma(pagina=3, chunk_index=2)]
        resultado = self._service_con_mocks(fragmentos, _ruta_neo4j())

        assert isinstance(resultado, ResultadoRecuperacion)
        assert resultado.pagina_paper == 3

    def test_chunk_index_llega_correctamente(self):
        fragmentos = [_fragmento_chroma(pagina=3, chunk_index=2)]
        resultado = self._service_con_mocks(fragmentos, _ruta_neo4j())

        assert resultado.chunk_index == 2

    def test_pagina_paper_none_cuando_metadata_sin_campo(self):
        fragmento_legacy = {
            "texto": "Texto sin chunks.",
            "metadata": {"doi": "10.1234/old", "doi_normalizado": "10_1234_old"},
            "similitud": 0.85,
        }
        resultado = self._service_con_mocks([fragmento_legacy], _ruta_neo4j())

        assert resultado.pagina_paper is None
        assert resultado.chunk_index is None

    def test_similitud_se_preserva(self):
        fragmentos = [_fragmento_chroma(pagina=1, chunk_index=0, similitud=0.77)]
        resultado = self._service_con_mocks(fragmentos, _ruta_neo4j())

        assert resultado.similitud == 0.77

    def test_metodo_es_hibrido_cuando_hay_fragmento(self):
        fragmentos = [_fragmento_chroma(pagina=2, chunk_index=1)]
        resultado = self._service_con_mocks(fragmentos, _ruta_neo4j())

        assert resultado.metodo == "hibrido"

    def test_pagina_paper_none_cuando_no_hay_fragmentos(self):
        resultado = self._service_con_mocks(fragmentos=[], ruta=_ruta_neo4j())

        assert resultado.pagina_paper is None
        assert resultado.chunk_index is None
        assert resultado.metodo in ("solo_grafo", "no_encontrado")

    def test_pagina_paper_none_cuando_no_hay_ruta_grafo(self):
        resultado = self._service_con_mocks(fragmentos=[], ruta=None)

        assert resultado.pagina_paper is None
        assert resultado.metodo == "no_encontrado"

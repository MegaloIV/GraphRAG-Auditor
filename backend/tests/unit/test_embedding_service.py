"""
Tests unitarios para la lógica de chunking de EmbeddingService.
No llama a ChromaDB ni a SentenceTransformer reales.
"""
import numpy as np
import pytest
from unittest.mock import MagicMock, patch, call

from app.services.externo.embedding_service import (
    EmbeddingService,
    _dividir_en_chunks,
    _es_chunk_basura,
    MAX_CHUNK_CHARS,
)


# ---------------------------------------------------------------------------
# Tests de _dividir_en_chunks
# ---------------------------------------------------------------------------

class TestDividirEnChunks:

    def test_split_por_form_feed(self):
        texto = "Página uno.\f Página dos.\f Página tres."
        chunks = _dividir_en_chunks(texto)
        assert len(chunks) == 3
        assert chunks[0] == "Página uno."
        assert chunks[1] == "Página dos."
        assert chunks[2] == "Página tres."

    def test_split_por_marcador_page_n(self):
        texto = "Contenido A.\nPage 1\nContenido B.\nPage 2\nContenido C."
        chunks = _dividir_en_chunks(texto)
        assert len(chunks) == 3

    def test_split_por_marcador_guiones(self):
        texto = "Intro.\n--- Page 1 ---\nCuerpo.\n--- Page 2 ---\nConclusion."
        chunks = _dividir_en_chunks(texto)
        assert len(chunks) == 3

    def test_fallback_por_tamano_respeta_parrafos(self):
        parrafo = "A" * 600 + "\n\n"
        texto = parrafo * 4  # ~2800 chars con saltos de párrafo
        chunks = _dividir_en_chunks(texto, max_chars=MAX_CHUNK_CHARS)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= MAX_CHUNK_CHARS

    def test_texto_corto_es_un_solo_chunk(self):
        texto = "Texto breve."
        chunks = _dividir_en_chunks(texto)
        assert len(chunks) == 1
        assert chunks[0] == "Texto breve."

    def test_ignora_chunks_vacios(self):
        texto = "\f\fContenido real.\f"
        chunks = _dividir_en_chunks(texto)
        assert len(chunks) == 1
        assert chunks[0] == "Contenido real."


# ---------------------------------------------------------------------------
# Tests de EmbeddingService.indexar_paper con 3 páginas simuladas
# ---------------------------------------------------------------------------

METADATA_BASE = {
    "doi": "10.1234/test.001",
    "doi_normalizado": "10_1234_test_001",
    "titulo": "Test Paper",
    "anio": 2024,
    "nivel_confianza": "alto",
    "referencia_id": "ref_1",
}

TEXTO_3_PAGINAS = (
    "Sleep deprivation impairs prefrontal cortex function and reduces working memory capacity "
    "in university students. Participants completed standardized cognitive assessments after "
    "controlled sleep restriction protocols administered across multiple experimental sessions.\f"
    "Significant correlations were observed between sleep duration and executive function scores. "
    "Participants with fewer than six hours of sleep showed a thirty percent reduction in "
    "sustained attention performance compared to the well-rested control group.\f"
    "These findings support the restorative theory of sleep and suggest that academic institutions "
    "should promote evidence-based sleep hygiene programs to improve student cognitive outcomes "
    "and overall academic performance across all disciplines."
)


@pytest.fixture
def service_mock():
    """EmbeddingService con ChromaDB y SentenceTransformer mockeados."""
    service = EmbeddingService.__new__(EmbeddingService)

    coleccion_mock = MagicMock()
    modelo_mock = MagicMock()
    modelo_mock.encode.return_value = np.zeros(384)

    service._coleccion = coleccion_mock
    service._modelo = modelo_mock
    service._cliente = MagicMock()
    return service, coleccion_mock, modelo_mock


class TestIndexarPaperChunks:

    def test_genera_3_chunks_con_paginas_1_2_3(self, service_mock):
        service, coleccion, modelo = service_mock

        result = service.indexar_paper(
            doi="10.1234/test.001",
            texto=TEXTO_3_PAGINAS,
            metadata=METADATA_BASE.copy(),
        )

        assert result is True
        coleccion.upsert.assert_called_once()
        kwargs = coleccion.upsert.call_args.kwargs

        assert len(kwargs["ids"]) == 3
        assert len(kwargs["metadatas"]) == 3

        paginas = [m["pagina"] for m in kwargs["metadatas"]]
        assert paginas == [1, 2, 3]

        chunk_indices = [m["chunk_index"] for m in kwargs["metadatas"]]
        assert chunk_indices == [0, 1, 2]

    def test_ids_tienen_formato_chunk(self, service_mock):
        service, coleccion, modelo = service_mock

        service.indexar_paper(
            doi="10.1234/test.001",
            texto=TEXTO_3_PAGINAS,
            metadata=METADATA_BASE.copy(),
        )

        ids = coleccion.upsert.call_args.kwargs["ids"]
        assert ids == [
            "10_1234_test_001_chunk_0",
            "10_1234_test_001_chunk_1",
            "10_1234_test_001_chunk_2",
        ]

    def test_metadata_base_se_preserva_en_cada_chunk(self, service_mock):
        service, coleccion, modelo = service_mock

        service.indexar_paper(
            doi="10.1234/test.001",
            texto=TEXTO_3_PAGINAS,
            metadata=METADATA_BASE.copy(),
        )

        for meta in coleccion.upsert.call_args.kwargs["metadatas"]:
            assert meta["doi"] == "10.1234/test.001"
            assert meta["titulo"] == "Test Paper"
            assert meta["nivel_confianza"] == "alto"

    def test_texto_vacio_retorna_false(self, service_mock):
        service, coleccion, _ = service_mock
        assert service.indexar_paper("10.1234/x", "", {}) is False
        coleccion.upsert.assert_not_called()

    def test_paper_ya_indexado_detecta_chunk_0(self, service_mock):
        service, coleccion, _ = service_mock
        coleccion.get.return_value = {"ids": ["10_1234_test_001_chunk_0"]}
        assert service.paper_ya_indexado("10.1234/test.001") is True

    def test_paper_no_indexado_retorna_false(self, service_mock):
        service, coleccion, _ = service_mock
        coleccion.get.return_value = {"ids": []}
        assert service.paper_ya_indexado("10.1234/nuevo") is False


# ---------------------------------------------------------------------------
# Tests de _es_chunk_basura
# ---------------------------------------------------------------------------

class TestEsChunkBasura:

    def test_boilerplate_publisher_note_es_descartado(self):
        chunk = (
            "Publisher's Note Springer Nature remains neutral with regard to jurisdictional "
            "claims in published maps and institutional affiliations of the authors involved."
        )
        assert _es_chunk_basura(chunk) is True

    def test_contenido_academico_real_no_es_descartado(self):
        chunk = (
            "This study investigates the relationship between sleep quality and cognitive "
            "performance in university students. Participants completed standardized assessments "
            "of working memory and executive function over a period of eight consecutive weeks. "
            "Results indicate a significant correlation between sleep duration and academic outcomes."
        )
        assert _es_chunk_basura(chunk) is False

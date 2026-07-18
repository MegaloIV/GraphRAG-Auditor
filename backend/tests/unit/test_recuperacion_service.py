"""
RecuperacionService — recuperación híbrida (grafo + pgvector).

Lo que se prueba aquí es la decisión de qué evidencia se entrega al juez y con
qué método. Neo4j y pgvector están mockeados; no se abre ninguna conexión ni se
calcula ningún embedding real.

La regla crítica: la búsqueda vectorial se restringe al DOI de la referencia que
la cita dice estar citando. Sin esa restricción, el sistema podría "encontrar
evidencia" en un paper distinto.
"""
from unittest.mock import patch

from app.services.recuperacion.recuperacion_service import RecuperacionService

DOI = "10.3390/educsci11040173"
DOI_CHUNKS = "10_3390_educsci11040173"

_RUTA = {
    "texto_cita": "(Edge et al., 2024)",
    "ref_id": "r1",
    "doi": DOI,
    "doi_raw": DOI,
    "autores": ["Edge, D."],
    "ref_titulo": "From Local to Global: A GraphRAG Approach",
    "ref_anio": 2024,
    "confianza_vinculo": 0.95,
}

_FRAGMENTO = {
    "contenido": "GraphRAG outperforms naive RAG on global sensemaking questions.",
    "similitud": 0.87,
    "pagina": 4,
    "chunk_index": 12,
}

_P_RUTA = "app.services.recuperacion.recuperacion_service.RecuperacionService._obtener_ruta_grafo"
_P_FRAG = "app.services.recuperacion.recuperacion_service.RecuperacionService._buscar_fragmentos"
_P_NODOS = "app.services.recuperacion.recuperacion_service.RecuperacionService._construir_nodos_grafo"
_P_COSER = "app.services.recuperacion.recuperacion_service.RecuperacionService._coser_ventana"


class TestConsultarCita:

    def test_con_doi_y_chunks_devuelve_evidencia_por_metodo_hibrido(self):
        with (
            patch(_P_RUTA, return_value=_RUTA),
            patch(_P_NODOS, return_value=[]),
            patch(_P_FRAG, return_value=[_FRAGMENTO]),
            patch(_P_COSER, return_value=_FRAGMENTO["contenido"]),
        ):
            r = RecuperacionService().consultar_cita("doc-1", "c1")

        assert r.metodo == "hibrido"
        assert r.fragmento_relevante == _FRAGMENTO["contenido"]
        assert r.similitud == 0.87
        assert r.doi_referencia == DOI
        assert r.referencia_id == "r1"

    def test_propaga_la_pagina_del_paper_de_la_que_sale_la_evidencia(self):
        with (
            patch(_P_RUTA, return_value=_RUTA),
            patch(_P_NODOS, return_value=[]),
            patch(_P_FRAG, return_value=[_FRAGMENTO]),
            patch(_P_COSER, return_value=_FRAGMENTO["contenido"]),
        ):
            r = RecuperacionService().consultar_cita("doc-1", "c1")

        assert r.pagina_paper == 4
        assert r.chunk_index == 12

    def test_la_busqueda_se_restringe_al_doi_de_la_referencia_citada(self):
        with (
            patch(_P_RUTA, return_value=_RUTA),
            patch(_P_NODOS, return_value=[]),
            patch(_P_COSER, return_value=""),
            patch(_P_FRAG, return_value=[_FRAGMENTO]) as buscar,
        ):
            RecuperacionService().consultar_cita("doc-1", "c1")

        _query, doi_usado, _n = buscar.call_args[0]
        assert doi_usado == DOI_CHUNKS

    def test_busca_por_la_aseveracion_del_tesista_cuando_existe(self):
        aseveracion = "GraphRAG mejora la recuperación en preguntas globales."
        with (
            patch(_P_RUTA, return_value=_RUTA),
            patch(_P_NODOS, return_value=[]),
            patch(_P_COSER, return_value=""),
            patch(_P_FRAG, return_value=[_FRAGMENTO]) as buscar,
        ):
            RecuperacionService().consultar_cita("doc-1", "c1", fragmento_oracion=aseveracion)

        query = buscar.call_args[0][0]
        assert query == aseveracion

    def test_sin_aseveracion_busca_por_el_texto_de_la_cita(self):
        with (
            patch(_P_RUTA, return_value=_RUTA),
            patch(_P_NODOS, return_value=[]),
            patch(_P_COSER, return_value=""),
            patch(_P_FRAG, return_value=[_FRAGMENTO]) as buscar,
        ):
            RecuperacionService().consultar_cita("doc-1", "c1", fragmento_oracion="   ")

        assert buscar.call_args[0][0] == "(Edge et al., 2024)"

    def test_sin_chunks_del_paper_cae_a_solo_grafo_y_sin_evidencia(self):
        with (
            patch(_P_RUTA, return_value=_RUTA),
            patch(_P_NODOS, return_value=[]),
            patch(_P_FRAG, return_value=[]),
        ):
            r = RecuperacionService().consultar_cita("doc-1", "c1")

        assert r.metodo == "solo_grafo"
        assert r.fragmento_relevante == ""
        assert r.similitud == 0.0
        assert r.referencia_id == "r1"  # la referencia existe, el texto no

    def test_una_cita_sin_referencia_vinculada_no_recupera_nada(self):
        with patch(_P_RUTA, return_value=None):
            r = RecuperacionService().consultar_cita("doc-1", "c1")

        assert r.metodo == "no_encontrado"
        assert r.referencia_id is None
        assert r.fragmento_relevante == ""

    def test_una_referencia_sin_doi_no_dispara_busqueda_vectorial(self):
        ruta_sin_doi = {**_RUTA, "doi": None, "doi_raw": None}
        with (
            patch(_P_RUTA, return_value=ruta_sin_doi),
            patch(_P_NODOS, return_value=[]),
            patch(_P_FRAG) as buscar,
        ):
            r = RecuperacionService().consultar_cita("doc-1", "c1")

        buscar.assert_not_called()
        assert r.metodo == "solo_grafo"

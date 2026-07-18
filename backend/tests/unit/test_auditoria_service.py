"""
AuditoriaService — emisión del veredicto por cita.

Ni el LLM ni Neo4j ni pgvector se tocan: todo está mockeado. Lo que se prueba es
la política del auditor, que es donde está el riesgo real del sistema:

  - sin referencia vinculada        → NO_INFO (nunca se inventa una fuente)
  - paper no indexado              → NO_INFO
  - referencia sin texto recuperado → NO_INFO (aunque la referencia exista)
  - con evidencia                   → se respeta el veredicto del juez

Un fallo aquí significaría dar por respaldada una cita sin haber leído la fuente.
"""
from unittest.mock import patch

from app.models.auditoria import VeredictoTipo
from app.services.auditoria.auditoria_service import AuditoriaService
from app.services.recuperacion.recuperacion_service import ResultadoRecuperacion

_P_INDEXADO = "app.services.auditoria.auditoria_service.supabase_vector_service.paper_ya_indexado"
_P_CONSULTAR = "app.services.auditoria.auditoria_service.recuperacion_service.consultar_cita"
_P_LLM = "app.services.auditoria.auditoria_service.AuditoriaService._llamar_llm"
_P_TRADUCIR = "app.services.auditoria.auditoria_service.AuditoriaService._traducir_evidencia"
_P_PERSISTIR = "app.services.auditoria.auditoria_service.AuditoriaService._persistir_veredicto"

DOI = "10.3390/educsci11040173"
EVIDENCIA = "GraphRAG outperforms naive RAG on global sensemaking questions."
ASEVERACION = "GraphRAG supera a RAG clásico en preguntas globales (Edge et al., 2024)."

_KWARGS = dict(
    documento_id="doc-1",
    cita_id="c1",
    texto_cita="(Edge et al., 2024)",
    pagina=12,
    ref_id="r1",
    ref_titulo="From Local to Global: A GraphRAG Approach",
    ref_anio=2024,
    autores=["Edge, D."],
)


def _resultado(metodo="hibrido", fragmento=EVIDENCIA, similitud=0.87, pagina_paper=4):
    return ResultadoRecuperacion(
        cita_id="c1",
        texto_cita="(Edge et al., 2024)",
        fragmento_relevante=fragmento,
        similitud=similitud,
        nodos_grafo=[],
        doi_referencia=DOI,
        referencia_id="r1",
        metodo=metodo,
        pagina_paper=pagina_paper,
    )


class TestCitaSinFuenteVerificable:
    """Sin fuente no hay veredicto favorable posible: siempre NO_INFO."""

    def test_sin_referencia_vinculada_devuelve_no_info(self):
        with patch(_P_PERSISTIR):
            v = AuditoriaService()._auditar_cita(
                **{**_KWARGS, "ref_id": None}, fragmento_oracion=ASEVERACION
            )
        assert v.veredicto == VeredictoTipo.NO_INFO
        assert v.metodo_recuperacion == "no_encontrado"
        assert "referencia" in v.justificacion.lower()

    def test_no_llama_al_juez_si_no_hay_referencia(self):
        with patch(_P_PERSISTIR), patch(_P_LLM) as llm:
            AuditoriaService()._auditar_cita(**{**_KWARGS, "ref_id": None})
        llm.assert_not_called()

    def test_paper_no_indexado_devuelve_no_info(self):
        with (
            patch(_P_PERSISTIR),
            patch(_P_INDEXADO, return_value=False),
            patch(_P_LLM) as llm,
        ):
            v = AuditoriaService()._auditar_cita(**_KWARGS, doi_verificado=DOI)
        assert v.veredicto == VeredictoTipo.NO_INFO
        assert "base de conocimiento" in v.justificacion
        llm.assert_not_called()

    def test_referencia_sin_texto_recuperado_devuelve_no_info(self):
        with (
            patch(_P_PERSISTIR),
            patch(_P_CONSULTAR, return_value=_resultado(metodo="solo_grafo", fragmento="", similitud=0.0)),
            patch(_P_LLM) as llm,
        ):
            v = AuditoriaService()._auditar_cita(**_KWARGS)
        assert v.veredicto == VeredictoTipo.NO_INFO
        assert v.metodo_recuperacion == "solo_grafo"
        assert v.referencia_id == "r1"  # la referencia existe; el texto no
        llm.assert_not_called()


class TestCitaConEvidencia:
    """Con evidencia recuperada, el veredicto es el del juez y se conserva la trazabilidad."""

    def test_respeta_el_veredicto_de_respaldo(self):
        with (
            patch(_P_PERSISTIR),
            patch(_P_INDEXADO, return_value=True),
            patch(_P_CONSULTAR, return_value=_resultado()),
            patch(_P_TRADUCIR, return_value="GraphRAG supera a RAG clásico."),
            patch(_P_LLM, return_value=(VeredictoTipo.SUPPORTS, "La fuente sostiene la afirmación.")),
        ):
            v = AuditoriaService()._auditar_cita(
                **_KWARGS, doi_verificado=DOI, fragmento_oracion=ASEVERACION
            )
        assert v.veredicto == VeredictoTipo.SUPPORTS
        assert v.justificacion == "La fuente sostiene la afirmación."

    def test_respeta_el_veredicto_de_refutacion(self):
        with (
            patch(_P_PERSISTIR),
            patch(_P_CONSULTAR, return_value=_resultado()),
            patch(_P_TRADUCIR, return_value=""),
            patch(_P_LLM, return_value=(VeredictoTipo.REFUTES, "La fuente afirma lo contrario.")),
        ):
            v = AuditoriaService()._auditar_cita(**_KWARGS, fragmento_oracion=ASEVERACION)
        assert v.veredicto == VeredictoTipo.REFUTES

    def test_adjunta_la_evidencia_y_la_pagina_del_paper(self):
        with (
            patch(_P_PERSISTIR),
            patch(_P_CONSULTAR, return_value=_resultado()),
            patch(_P_TRADUCIR, return_value=""),
            patch(_P_LLM, return_value=(VeredictoTipo.SUPPORTS, "Respaldada.")),
        ):
            v = AuditoriaService()._auditar_cita(**_KWARGS, fragmento_oracion=ASEVERACION)
        assert v.fragmento_evidencia == EVIDENCIA
        assert v.pagina_paper == 4
        assert v.similitud == 0.87
        assert v.metodo_recuperacion == "hibrido"

    def test_conserva_la_aseveracion_del_tesista_en_el_veredicto(self):
        with (
            patch(_P_PERSISTIR),
            patch(_P_CONSULTAR, return_value=_resultado()),
            patch(_P_TRADUCIR, return_value=""),
            patch(_P_LLM, return_value=(VeredictoTipo.SUPPORTS, "Respaldada.")),
        ):
            v = AuditoriaService()._auditar_cita(**_KWARGS, fragmento_oracion=ASEVERACION)
        assert v.fragmento_oracion == ASEVERACION

    def test_arrastra_los_datos_de_la_fuente_usada(self):
        with (
            patch(_P_PERSISTIR),
            patch(_P_CONSULTAR, return_value=_resultado()),
            patch(_P_TRADUCIR, return_value=""),
            patch(_P_LLM, return_value=(VeredictoTipo.SUPPORTS, "Respaldada.")),
        ):
            v = AuditoriaService()._auditar_cita(**_KWARGS, fragmento_oracion=ASEVERACION)
        assert v.doi_referencia == DOI
        assert v.titulo_referencia == "From Local to Global: A GraphRAG Approach"
        assert v.anio_referencia == 2024
        assert v.autores_referencia == ["Edge, D."]

    def test_el_juez_recibe_la_aseveracion_y_la_evidencia(self):
        with (
            patch(_P_PERSISTIR),
            patch(_P_CONSULTAR, return_value=_resultado()),
            patch(_P_TRADUCIR, return_value=""),
            patch(_P_LLM, return_value=(VeredictoTipo.SUPPORTS, "Respaldada.")) as llm,
        ):
            AuditoriaService()._auditar_cita(**_KWARGS, fragmento_oracion=ASEVERACION)
        kwargs = llm.call_args.kwargs
        assert kwargs["fragmento_oracion"] == ASEVERACION
        assert kwargs["fragmento"] == EVIDENCIA


class TestParsearRespuestaDelJuez:
    """El parseo de la respuesta del LLM nunca debe romper la auditoría."""

    def test_parsea_un_respaldo(self):
        v, j = AuditoriaService._parsear_respuesta_llm(
            "VERDICT: SUPPORTS\nJUSTIFICATION: El fragmento contiene la cifra citada."
        )
        assert v == VeredictoTipo.SUPPORTS
        assert j == "El fragmento contiene la cifra citada."

    def test_parsea_una_refutacion(self):
        v, _ = AuditoriaService._parsear_respuesta_llm(
            "VERDICT: REFUTES\nJUSTIFICATION: La fuente afirma lo contrario."
        )
        assert v == VeredictoTipo.REFUTES

    def test_parsea_una_falta_de_evidencia(self):
        v, _ = AuditoriaService._parsear_respuesta_llm(
            "VERDICT: NO_INFO\nJUSTIFICATION: El fragmento no trata la afirmación."
        )
        assert v == VeredictoTipo.NO_INFO

    def test_una_respuesta_inesperada_cae_en_no_info(self):
        v, _ = AuditoriaService._parsear_respuesta_llm("No estoy seguro de esta cita.")
        assert v == VeredictoTipo.NO_INFO

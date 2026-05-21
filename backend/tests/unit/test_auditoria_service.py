"""
Tests unitarios para AuditoriaService.
No llaman al LLM real ni a Neo4j — todo se mockea a nivel de módulo.
"""
from unittest.mock import MagicMock, patch

from app.services.auditoria.auditoria_service import AuditoriaService
from app.models.auditoria import VeredictoTipo
from app.services.recuperacion.recuperacion_service import ResultadoRecuperacion

_PATCH_RECUPERAR = "app.services.auditoria.auditoria_service.recuperacion_service.consultar_cita"
_PATCH_LLM       = "app.services.auditoria.auditoria_service.AuditoriaService._llamar_llm"

_RESULTADO_OK = ResultadoRecuperacion(
    cita_id="cita-1",
    texto_cita="García et al. (2021)",
    fragmento_relevante="Texto real del paper.",
    similitud=0.9,
    nodos_grafo=[],
    doi_referencia="10.1234/test",
    referencia_id="ref-1",
    metodo="chroma",
)

_KWARGS_COMUNES = dict(
    documento_id="doc-1",
    cita_id="cita-1",
    texto_cita="García et al. (2021)",
    pagina=5,
    ref_id="ref-1",
    ref_titulo="Título del paper",
    ref_anio=2021,
    autores=["García, J."],
)


class TestAuditarCitaFragmentoOracion:
    """_auditar_cita debe propagar fragmento_oracion al VeredictoAuditoria."""

    def test_fragmento_oracion_se_incluye_en_veredicto(self):
        fragmento = "García et al. (2021) demuestran que el modelo es válido en entornos reales."

        with (
            patch(_PATCH_RECUPERAR, return_value=_RESULTADO_OK),
            patch(_PATCH_LLM, return_value=(VeredictoTipo.VALIDA, "La cita es fiel.")),
        ):
            veredicto = AuditoriaService()._auditar_cita(
                **_KWARGS_COMUNES,
                fragmento_oracion=fragmento,
            )

        assert veredicto.fragmento_oracion == fragmento

    def test_fragmento_oracion_vacio_se_propaga_igual(self):
        with (
            patch(_PATCH_RECUPERAR, return_value=_RESULTADO_OK),
            patch(_PATCH_LLM, return_value=(VeredictoTipo.VALIDA, "Justificación.")),
        ):
            veredicto = AuditoriaService()._auditar_cita(
                **_KWARGS_COMUNES,
                fragmento_oracion="",
            )

        assert veredicto.fragmento_oracion == ""

"""
doi_utils — normalización de DOI.

Es la pieza que decide si una referencia de la tesis y un paper de CrossRef son
la misma fuente, y bajo qué clave se guardan sus chunks en pgvector. Los DOIs
usados aquí son DOIs reales tomados de la bibliografía de la tesis.
"""
import re

from app.services.externo.doi_utils import doi_para_chunks, normalizar_doi

# DOI real de una de las referencias de la tesis.
DOI_TESIS = "10.3390/educsci11040173"


class TestNormalizarDOI:

    def test_un_doi_limpio_no_cambia(self):
        assert normalizar_doi(DOI_TESIS) == DOI_TESIS

    def test_quita_el_prefijo_https_doi_org(self):
        assert normalizar_doi(f"https://doi.org/{DOI_TESIS}") == DOI_TESIS

    def test_quita_el_prefijo_dx_doi_org(self):
        assert normalizar_doi(f"http://dx.doi.org/{DOI_TESIS}") == DOI_TESIS

    def test_quita_el_prefijo_doi_dos_puntos(self):
        assert normalizar_doi(f"doi:{DOI_TESIS}") == DOI_TESIS

    def test_pasa_a_minusculas(self):
        assert normalizar_doi("10.48550/arXiv.2602.05930") == "10.48550/arxiv.2602.05930"

    def test_recorta_espacios_y_barra_final(self):
        assert normalizar_doi(f"  {DOI_TESIS}/  ") == DOI_TESIS

    def test_sin_doi_devuelve_cadena_vacia(self):
        assert normalizar_doi(None) == ""
        assert normalizar_doi("") == ""

    def test_dos_escrituras_del_mismo_doi_normalizan_igual(self):
        # El mismo paper citado con URL y sin ella debe resolver a la misma fuente.
        assert normalizar_doi(f"https://doi.org/{DOI_TESIS.upper()}") == normalizar_doi(DOI_TESIS)


class TestDOIParaChunks:

    def test_construye_la_clave_del_vector_store(self):
        assert doi_para_chunks(DOI_TESIS) == "10_3390_educsci11040173"

    def test_la_clave_no_contiene_barras_ni_puntos(self):
        clave = doi_para_chunks("10.48550/arXiv.2602.05930")
        assert "/" not in clave and "." not in clave

    def test_dois_distintos_producen_claves_distintas(self):
        assert doi_para_chunks(DOI_TESIS) != doi_para_chunks("10.18060/26528")

    def test_la_clave_es_estable_entre_llamadas(self):
        assert doi_para_chunks(DOI_TESIS) == doi_para_chunks(DOI_TESIS)


class TestValidacionDelFormato:
    """
    Un valor que no es un DOI no debe viajar en el campo DOI: después se
    consulta contra CrossRef y Unpaywall, y falla siempre. Se vieron
    identificadores de arXiv sueltos guardados como doi_verificado.
    """

    def test_descarta_lo_que_no_es_un_doi(self):
        assert normalizar_doi("texto cualquiera") == ""
        assert normalizar_doi("arXiv:1512.03385v1 [cs.CV] y mas cosas raras") != "arxiv:1512.03385v1 [cs.cv]"

    def test_convierte_un_identificador_de_arxiv_a_su_doi(self):
        # El paper es público: convertirlo lo vuelve descargable.
        assert normalizar_doi("arXiv:1512.03385v1 [cs.CV]") == "10.48550/arxiv.1512.03385"
        assert normalizar_doi("arxiv:2511.11035") == "10.48550/arxiv.2511.11035"

    def test_conserva_un_doi_con_parentesis(self):
        # Formato antiguo de Elsevier; es válido y no debe descartarse.
        assert normalizar_doi("10.1016/S0190-9622(94)70061-3") == "10.1016/s0190-9622(94)70061-3"

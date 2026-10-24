"""
fuentes_abiertas y validación del DOI declarado — las piezas que deciden si un
texto conseguido es de verdad la fuente citada.

Indexar un paper equivocado es peor que no indexar nada: el juez emitiría
veredictos contra una fuente que no es la citada. Los casos salen de tesis
reales: un DOI transcrito por el tesista que pertenece a otro paper, un título
traducido al español, URLs que son solo la portada de un sitio y URLs partidas
por el salto de línea del PDF.
"""
from types import SimpleNamespace

from app.models.grafo import ReferenciaAPA
from app.services.externo import fuentes_abiertas as F
from app.services.externo.verificacion_service import VerificacionService


def _ref(titulo, autores, anio, doi=None):
    return ReferenciaAPA(referencia_id="r1", titulo=titulo, autores=autores, anio=anio,
                         doi=doi, fuente=None, url=None, datos_incompletos=False,
                         campos_faltantes=[])


class TestEsUrlGenerica:

    def test_el_dominio_solo_es_generico(self):
        assert F.es_url_generica("https://www.datagy.io")
        assert F.es_url_generica("https://www.graphite-note.com/")

    def test_el_indice_de_noticias_es_generico(self):
        assert F.es_url_generica("https://med.stanford.edu/news")

    def test_una_pagina_concreta_no_es_generica(self):
        assert not F.es_url_generica("https://www.cancer.org/cancer/melanoma-skin-cancer/about.html")


class TestUrlDesdeBibliografia:

    BIBLIO = (
        "American Cancer Society. (2022). Survival Rates for Melanoma Skin Cancer. "
        "https://www.cancer.org/cancer/melanoma-skin-cancer/detection-diagnosis-staging/survival-\n"
        "rates.html American Cancer Society. (2023). Key Statistics for Basal and Squamous "
        "Cell Skin Cancers. https://www.cancer.org/cancer/basal-and-squamous-cell-skin-cancer/about/key-\n"
        "statistics.html Esteva, A. (2017). Dermatologist-level classification. Nature."
    )

    def test_recompone_la_url_partida_por_el_salto_de_linea(self):
        url = F.url_desde_bibliografia(self.BIBLIO, "Survival Rates for Melanoma Skin Cancer")
        assert url == ("https://www.cancer.org/cancer/melanoma-skin-cancer/"
                       "detection-diagnosis-staging/survival-rates.html")

    def test_no_se_lleva_el_autor_de_la_entrada_siguiente(self):
        url = F.url_desde_bibliografia(self.BIBLIO, "Key Statistics for Basal and Squamous Cell Skin Cancers")
        assert url.endswith("/key-statistics.html")

    def test_una_entrada_sin_url_no_toma_la_de_otra(self):
        biblio = ("Castillo, G. (2016). Interfaz de composición de consultas a bases de datos. "
                  "Elgohary, A. (2020). Speak to your Parser. https://doi.org/10.18653/v1/2020.acl-main.187")
        assert F.url_desde_bibliografia(biblio, "Interfaz de composición de consultas a bases de datos") is None

    def test_ignora_los_enlaces_de_zotero_que_envuelven_la_entrada(self):
        biblio = ("[Oliveira, A. (2024). Text-to-SQL Meets the Real-World.](https://www.zotero.org/google-docs/?mc) "
                  "[https://doi.org/10.5220/0012555200003690](https://www.zotero.org/google-docs/?mc)")
        url = F.url_desde_bibliografia(biblio, "Text-to-SQL Meets the Real-World")
        assert url == "https://doi.org/10.5220/0012555200003690"

    def test_sin_texto_no_rompe(self):
        assert F.url_desde_bibliografia("", "Cualquier título largo aquí") is None


class TestConcordancia:

    CABECERA = ("Comprehensive Review of Edge Computing for Power Systems: State of the Art, "
                "Architecture, and Applications. Fatih Yıldırım, Yunus Yalman, Kamil Çağatay Bayındır. "
                "Applied Sciences 2025, 15, 4592. Abstract: Edge computing ...")

    def test_el_titulo_en_la_cabecera_concuerda(self):
        c = F.concordancia("Comprehensive Review of Edge Computing for Power Systems", self.CABECERA)
        assert c == 1.0

    def test_un_titulo_traducido_se_acepta_por_autores_y_anio(self):
        c = F.concordancia(
            "Revisión integral de la computación de borde para sistemas de energía",
            self.CABECERA, ["Yıldırım, F.", "Yalman, Y.", "Bayındır, K. Ç."], 2025,
        )
        assert c >= 0.6

    def test_otro_paper_no_concuerda(self):
        c = F.concordancia(
            "A Deep Learning Method of Credit Card Fraud Detection Based on Continuous-Coupled Neural Networks",
            self.CABECERA, ["Wu, Y.", "Wang, L.", "Li, H."], 2025,
        )
        assert c < 0.6

    def test_las_palabras_del_titulo_en_el_cuerpo_no_cuentan(self):
        # La ventana corta evita que un título genérico «aparezca» esparcido
        # por el cuerpo de cualquier paper del área.
        cuerpo = "Intrusion detection. " + "x " * 2000 + "deep learning method credit card fraud detection neural networks"
        c = F.concordancia("A Deep Learning Method of Credit Card Fraud Detection Neural Networks", cuerpo)
        assert c < 0.6


class TestDoiCorresponde:

    def test_el_doi_de_otro_paper_no_corresponde(self):
        # Caso real (t02): el tesista escribió el DOI de otra referencia de su tesis.
        ref = _ref("A Deep Learning Method of Credit Card Fraud Detection Based on Continuous-Coupled Neural Networks",
                   ["Wu, Y.", "Wang, L.", "Li, H."], 2025, "10.3390/sym15030568")
        crossref = SimpleNamespace(
            titulo_oficial="Hybrid Intrusion Detection System Based on Combination of Random Forest and Autoencoder",
            autores_oficiales=["Wang, C.", "Sun, Y.", "Wang, W.", "Liu, H."], anio_oficial=2023)
        assert not VerificacionService._doi_corresponde(ref, crossref)

    def test_el_titulo_traducido_corresponde_por_autor_y_anio(self):
        ref = _ref("Revisión integral de la computación de borde para sistemas de energía",
                   ["Yıldırım, F.", "Yalman, Y."], 2025, "10.3390/app15084592")
        crossref = SimpleNamespace(
            titulo_oficial="Comprehensive Review of Edge Computing for Power Systems: State of the Art, Architecture, and Applications",
            autores_oficiales=["Yıldırım, Fatih", "Yalman, Yunus"], anio_oficial=2025)
        assert VerificacionService._doi_corresponde(ref, crossref)

    def test_un_titulo_truncado_corresponde(self):
        ref = _ref("Global cancer statistics 2018: GLOBOCAN estimates of incidence and mortality worldwide",
                   ["Bray, F."], 2018)
        crossref = SimpleNamespace(
            titulo_oficial=("Global cancer statistics 2018: GLOBOCAN estimates of incidence and mortality "
                            "worldwide for 36 cancers in 185 countries"),
            autores_oficiales=["Bray, Freddie"], anio_oficial=2018)
        assert VerificacionService._doi_corresponde(ref, crossref)

    def test_sin_titulo_oficial_no_se_puede_refutar(self):
        ref = _ref("Cualquier trabajo citado por el tesista", ["Pérez, J."], 2020)
        assert VerificacionService._doi_corresponde(
            ref, SimpleNamespace(titulo_oficial="", autores_oficiales=[], anio_oficial=None))


class TestEsCatalogoDeLibro:

    def test_una_ficha_de_google_books_es_catalogo(self):
        assert F.es_catalogo_de_libro(
            "https://www.google.com.pe/books/edition/Mastering_Microsoft_Fabric/yoOj0QEACAAJ?hl=es")
        assert F.es_catalogo_de_libro("https://books.google.com/books?id=abc")

    def test_una_pagina_de_documentacion_no_es_catalogo(self):
        assert not F.es_catalogo_de_libro("https://learn.microsoft.com/es-es/fabric/onelake/onelake-overview")

"""
LocalizacionService — dónde aparece cada cita dentro del PDF.

Se prueba contra el PDF real de la tesis (fixture `ruta_pdf`). Lo que importa
no es que haya un rectángulo, sino que el rectángulo caiga SOBRE la cita: un
resaltado en el lugar equivocado es peor que no tener resaltado, porque la
revisión humana lo da por bueno.
"""
import fitz
import pytest

from app.services.ingesta.localizacion_service import (
    _buscar_en_documento,
    _canon,
    _indexar_pagina,
    _rects_de_span,
    localizacion_service,
)


@pytest.fixture(scope="module")
def paginas(ruta_pdf):
    with fitz.open(str(ruta_pdf)) as doc:
        return [_indexar_pagina(page, n) for n, page in enumerate(doc, start=1)]


class TestCanon:

    def test_no_cambia_la_longitud(self):
        # El mapa de offsets se rompe si la canonicalización desplaza caracteres.
        for crudo in ["Peña–López", "“Cita”", "a b", "Johnson-Laird"]:
            assert len(_canon(crudo)) == len(crudo)

    def test_el_guion_equivale_a_un_espacio(self):
        assert _canon("Johnson-Laird") == _canon("Johnson Laird")


class TestIndicePagina:

    def test_la_pagina_se_indexa_como_texto_corrido(self, paginas):
        assert paginas, "el PDF de prueba debe tener páginas"
        assert any(len(p.texto) > 200 for p in paginas)

    def test_cada_tramo_apunta_a_un_rectangulo_valido(self, paginas):
        pagina = max(paginas, key=lambda p: len(p.spans))
        for ini, fin, rect in pagina.spans:
            assert 0 <= ini < fin <= len(pagina.texto)
            assert rect[0] <= rect[2] and rect[1] <= rect[3]

    def test_el_texto_de_un_tramo_coincide_con_el_indice(self, paginas):
        pagina = max(paginas, key=lambda p: len(p.spans))
        ini, fin, _ = pagina.spans[len(pagina.spans) // 2]
        assert pagina.texto[ini:fin].strip() != ""


class TestBusqueda:

    def test_encuentra_una_cadena_que_cruza_un_salto_de_linea(self, paginas):
        # Se toma un tramo largo del índice (que con seguridad cruza renglones)
        # y se comprueba que la búsqueda lo reencuentra.
        pagina = max(paginas, key=lambda p: len(p.texto))
        aguja = pagina.texto[100:190]
        hallazgos = _buscar_en_documento([pagina], aguja)
        assert hallazgos, "una cadena tomada de la propia página debe hallarse"

    def test_una_cadena_inexistente_no_devuelve_nada(self, paginas):
        assert _buscar_en_documento(paginas, "zzqxw inexistente 12345") == []

    def test_una_aguja_demasiado_corta_se_ignora(self, paginas):
        assert _buscar_en_documento(paginas, "de") == []


class TestRects:

    def test_el_rectangulo_cubre_el_texto_buscado(self, ruta_pdf, paginas):
        pagina = max(paginas, key=lambda p: len(p.texto))
        aguja = pagina.texto[300:340]
        hallazgos = _buscar_en_documento([pagina], aguja)
        assert hallazgos
        _, ini, fin = hallazgos[0]
        rects = _rects_de_span(pagina, ini, fin)
        assert rects
        with fitz.open(str(ruta_pdf)) as doc:
            bajo = ""
            for r in rects:
                caja = fitz.Rect(r.x0 - 2, r.y0 - 2, r.x1 + 2, r.y1 + 2)
                bajo += " " + doc[r.pagina - 1].get_textbox(caja)
        # La primera palabra completa de la aguja debe estar bajo el resaltado.
        primera = aguja.split()[1] if len(aguja.split()) > 1 else aguja.split()[0]
        assert primera in _canon(bajo)

    def test_una_cita_en_dos_renglones_devuelve_un_rect_por_renglon(self, paginas):
        pagina = max(paginas, key=lambda p: len(p.texto))
        # Un tramo largo casi con seguridad abarca más de un renglón.
        rects = _rects_de_span(pagina, 0, 400)
        assert len(rects) >= 2


class TestLocalizarCita:

    def test_sin_texto_no_se_localiza(self, paginas):
        u = localizacion_service._localizar_cita(
            paginas, {"cita_id": "c1", "texto_cita": "", "texto_original": "",
                      "fragmento": "", "pagina": 1}, {}, {},
        )
        assert u.metodo == "no_hallada" and u.pagina_real is None

    def test_una_cita_inexistente_se_reporta_como_no_hallada(self, paginas):
        u = localizacion_service._localizar_cita(
            paginas, {"cita_id": "c1", "texto_cita": "(Zzqxw, 1899)",
                      "texto_original": "(Zzqxw, 1899)", "fragmento": "", "pagina": 1}, {}, {},
        )
        assert u.metodo == "no_hallada"
        assert u.rects == []

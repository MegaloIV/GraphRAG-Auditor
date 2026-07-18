"""
PDFExtractionService — validación, extracción de texto y detección de secciones.

Se ejecuta contra el PDF real de la tesis (67 páginas). Sin red ni servicios
externos: PyMuPDF trabaja en local.
"""
import pytest

from app.core.config import get_settings
from app.models.ingesta import TipoSeccion
from app.services.ingesta.pdf_service import PDFNoProcessableError, pdf_service

settings = get_settings()


class TestValidarPDF:
    """validar_pdf rechaza lo que no debe entrar al pipeline."""

    def test_pdf_real_es_valido(self, ruta_pdf):
        contenido = ruta_pdf.read_bytes()
        pdf_service.validar_pdf(contenido, "tesis.pdf")  # no debe lanzar

    def test_rechaza_extension_que_no_es_pdf(self, ruta_pdf):
        contenido = ruta_pdf.read_bytes()
        with pytest.raises(PDFNoProcessableError) as exc:
            pdf_service.validar_pdf(contenido, "tesis.docx")
        assert exc.value.codigo == "FORMATO_INVALIDO"
        assert exc.value.accion  # el error siempre trae acción sugerida

    def test_rechaza_archivo_que_supera_el_limite(self):
        demasiado_grande = b"%PDF" + b"\0" * settings.max_pdf_size_bytes
        with pytest.raises(PDFNoProcessableError) as exc:
            pdf_service.validar_pdf(demasiado_grande, "tesis.pdf")
        assert exc.value.codigo == "ARCHIVO_DEMASIADO_GRANDE"
        assert str(settings.max_pdf_size_mb) in exc.value.mensaje

    def test_rechaza_contenido_sin_cabecera_pdf(self):
        with pytest.raises(PDFNoProcessableError) as exc:
            pdf_service.validar_pdf(b"esto no es un PDF", "tesis.pdf")
        assert exc.value.codigo == "PDF_CORRUPTO"


class TestExtraerTexto:
    """extraer_texto sobre la tesis real."""

    def test_devuelve_el_numero_real_de_paginas(self, paginas_tesis):
        assert paginas_tesis == 67

    def test_extrae_el_cuerpo_completo_del_documento(self, texto_tesis):
        assert len(texto_tesis) > 100_000

    def test_el_texto_conserva_el_titulo_de_la_tesis(self, texto_tesis):
        assert "GraphRAG" in texto_tesis

    def test_el_texto_no_arrastra_caracteres_invisibles(self, texto_tesis):
        invisibles = ["​", "‌", "‍", "﻿", "­"]
        assert not any(ch in texto_tesis for ch in invisibles)

    def test_segunda_llamada_usa_la_cache_en_disco(self, ruta_pdf, texto_tesis):
        cache = ruta_pdf.with_suffix(".md")
        assert cache.exists()  # la extracción previa dejó la caché
        texto_2, paginas_2 = pdf_service.extraer_texto(ruta_pdf)
        assert texto_2 == texto_tesis
        assert paginas_2 == 67


class TestObtenerTOC:
    """obtener_toc nunca rompe el pipeline: si no hay índice, devuelve lista vacía."""

    def test_la_tesis_no_trae_indice_embebido(self, ruta_pdf):
        assert pdf_service.obtener_toc(ruta_pdf) == []

    def test_ruta_inexistente_devuelve_lista_vacia(self, tmp_path):
        assert pdf_service.obtener_toc(tmp_path / "no_existe.pdf") == []


class TestDetectarSecciones:
    """detectar_secciones sobre la estructura real de la tesis."""

    def test_detecta_la_seccion_de_referencias(self, texto_tesis, paginas_tesis):
        estructura = pdf_service.detectar_secciones(texto_tesis, paginas_tesis, "doc-1")
        assert estructura.tiene_seccion_referencias is True

    def test_el_titulo_detectado_es_el_de_la_tesis(self, texto_tesis, paginas_tesis):
        estructura = pdf_service.detectar_secciones(texto_tesis, paginas_tesis, "doc-1")
        refs = [s for s in estructura.secciones if s.tipo == TipoSeccion.REFERENCIAS]
        assert len(refs) == 1
        assert "Referencias bibliográficas" in refs[0].titulo_detectado
        assert refs[0].tiene_referencias is True

    def test_sin_referencias_emite_advertencia(self, paginas_tesis):
        estructura = pdf_service.detectar_secciones(
            "# Introducción\n\nTexto sin bibliografía.", paginas_tesis, "doc-1"
        )
        assert estructura.tiene_seccion_referencias is False
        assert estructura.advertencia is not None

    def test_las_paginas_de_la_seccion_estan_dentro_del_documento(self, texto_tesis, paginas_tesis):
        estructura = pdf_service.detectar_secciones(texto_tesis, paginas_tesis, "doc-1")
        for seccion in estructura.secciones:
            assert 1 <= seccion.pagina_inicio <= paginas_tesis
            assert seccion.pagina_inicio <= seccion.pagina_fin <= paginas_tesis


class TestNormalizarYClasificar:
    """Helpers de clasificación, con los encabezados tal como salen del markdown."""

    def test_normalizar_quita_almohadillas_y_negritas(self):
        assert pdf_service._normalizar_titulo("## **7. Referencias**") == "7. Referencias"

    def test_clasifica_el_encabezado_real_de_la_tesis(self):
        assert pdf_service._clasificar_seccion("7. Referencias bibliográficas") == TipoSeccion.REFERENCIAS

    def test_clasifica_introduccion(self):
        assert pdf_service._clasificar_seccion("Introducción") == TipoSeccion.INTRODUCCION

    def test_un_titulo_cualquiera_queda_como_desconocido(self):
        assert pdf_service._clasificar_seccion("Agradecimientos") == TipoSeccion.DESCONOCIDO

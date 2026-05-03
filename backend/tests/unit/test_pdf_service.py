"""
Tests unitarios para el servicio de extracción PDF.
Cubre RN-001 (tiempo) y RN-002 (mensajes de error).
No requiere PDFs reales ni conexión a servicios externos.
"""
import pytest
from app.services.ingesta.pdf_service import PDFExtractionService, PDFNoProcessableError


@pytest.fixture
def service():
    return PDFExtractionService()


class TestValidarPDF:
    """RN-002: Mensajes de error claros ante PDF no procesable."""

    def test_rechaza_extension_incorrecta(self, service):
        contenido = b"%PDF-1.4 contenido valido"
        with pytest.raises(PDFNoProcessableError) as exc:
            service.validar_pdf(contenido, "documento.docx")
        assert exc.value.codigo == "FORMATO_INVALIDO"
        assert exc.value.accion is not None
        # RN-002: no debe exponer detalles técnicos
        assert "Exception" not in exc.value.mensaje
        assert "traceback" not in exc.value.mensaje.lower()

    def test_rechaza_archivo_demasiado_grande(self, service):
        contenido = b"%PDF" + b"x" * (11 * 1024 * 1024)
        with pytest.raises(PDFNoProcessableError) as exc:
            service.validar_pdf(contenido, "grande.pdf")
        assert exc.value.codigo == "ARCHIVO_DEMASIADO_GRANDE"
        assert "MB" in exc.value.mensaje

    def test_rechaza_archivo_corrupto(self, service):
        contenido = b"esto no es un pdf para nada"
        with pytest.raises(PDFNoProcessableError) as exc:
            service.validar_pdf(contenido, "corrupto.pdf")
        assert exc.value.codigo == "PDF_CORRUPTO"

    def test_acepta_pdf_valido(self, service):
        contenido = b"%PDF-1.4 " + b"contenido" * 100
        # No debe lanzar ninguna excepción
        service.validar_pdf(contenido, "valido.pdf")

    def test_mensaje_error_incluye_accion_sugerida(self, service):
        contenido = b"archivo corrupto"
        with pytest.raises(PDFNoProcessableError) as exc:
            service.validar_pdf(contenido, "test.pdf")
        # Siempre debe decirle al usuario qué hacer
        assert exc.value.accion is not None
        assert len(exc.value.accion) > 10


class TestDetectarSecciones:
    """HU-002: Detección de secciones y advertencia sin referencias."""

    def test_detecta_seccion_referencias_en_espanol(self, service):
        texto = "# Introducción\nContenido...\n\n# Referencias\n- García, 2020..."
        resultado = service.detectar_secciones(texto, num_paginas=5, documento_id="test-123")
        assert resultado.tiene_seccion_referencias is True
        assert resultado.advertencia is None

    def test_detecta_seccion_references_en_ingles(self, service):
        texto = "# Introduction\nContent...\n\n# References\n- Smith, 2021..."
        resultado = service.detectar_secciones(texto, num_paginas=4, documento_id="test-123")
        assert resultado.tiene_seccion_referencias is True

    def test_detecta_bibliografia(self, service):
        texto = "# Introducción\nContenido...\n\n# Bibliografía\n- López, 2019..."
        resultado = service.detectar_secciones(texto, num_paginas=3, documento_id="test-123")
        assert resultado.tiene_seccion_referencias is True

    def test_advierte_cuando_no_hay_referencias(self, service):
        texto = "# Introducción\nContenido...\n\n# Conclusión\nFin del documento."
        resultado = service.detectar_secciones(texto, num_paginas=3, documento_id="test-123")
        assert resultado.tiene_seccion_referencias is False
        assert resultado.advertencia is not None
        assert len(resultado.advertencia) > 0

    def test_retorna_total_paginas_correcto(self, service):
        texto = "# Introducción\nTexto de prueba"
        resultado = service.detectar_secciones(texto, num_paginas=10, documento_id="test-123")
        assert resultado.total_paginas == 10

    def test_retorna_documento_id_correcto(self, service):
        texto = "# Referencias\n- Autor, 2020"
        resultado = service.detectar_secciones(texto, num_paginas=2, documento_id="mi-id-123")
        assert resultado.documento_id == "mi-id-123"
"""
Contrato de la API: lo que el sistema promete a quien lo consume.

Los casos de la carga de documentos se derivan por partición de equivalencia
(una clase de entrada, un caso) y por valores límite sobre el tamaño máximo
admitido. Los del documento inexistente comprueban que el sistema responde con
un error explícito y nunca con un fallo interno.
"""
import pytest

from .conftest import LIMITE_MB

CABECERA_PDF = b"%PDF-1.7\n"


class TestDisponibilidadDelServicio:

    def test_el_servicio_responde_que_esta_sano(self, api):
        r = api.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_la_raiz_identifica_el_servicio_y_su_version(self, api):
        r = api.get("/")
        assert r.status_code == 200
        assert r.json()["nombre"] == "GraphRAG Auditor API"
        assert r.json()["estado"] == "operativo"

    def test_el_contrato_openapi_esta_publicado(self, api):
        r = api.get("/openapi.json")
        assert r.status_code == 200
        rutas = r.json()["paths"]
        assert "/api/v1/ingesta/cargar" in rutas
        assert "/api/v1/auditoria/{documento_id}/auditar" in rutas


class TestCargaDeDocumento:
    """Partición de equivalencia y valores límite sobre POST /ingesta/cargar."""

    def test_una_peticion_sin_archivo_es_rechazada(self, api):
        r = api.request("POST", "/api/v1/ingesta/cargar", descripcion_entrada="(sin archivo)")
        assert r.status_code == 422

    def test_un_archivo_que_no_es_pdf_es_rechazado(self, api, pdf_tesis):
        r = api.post(
            "/api/v1/ingesta/cargar",
            files={"archivo": ("tesis.docx", pdf_tesis, "application/msword")},
            descripcion_entrada=f"archivo=tesis.docx (<{len(pdf_tesis)} bytes>)",
        )
        assert r.status_code == 422
        assert r.json()["detail"]["codigo"] == "FORMATO_INVALIDO"
        assert r.json()["detail"]["accion_sugerida"]

    def test_un_pdf_con_contenido_corrupto_es_rechazado(self, api):
        contenido = b"esto no es un PDF"
        r = api.post(
            "/api/v1/ingesta/cargar",
            files={"archivo": ("tesis.pdf", contenido, "application/pdf")},
            descripcion_entrada=f"archivo=tesis.pdf ({contenido!r})",
        )
        assert r.status_code == 422
        assert r.json()["detail"]["codigo"] == "PDF_CORRUPTO"

    def test_un_archivo_vacio_es_rechazado(self, api):
        r = api.post(
            "/api/v1/ingesta/cargar",
            files={"archivo": ("tesis.pdf", b"", "application/pdf")},
            descripcion_entrada="archivo=tesis.pdf (<0 bytes>)",
        )
        assert r.status_code == 422
        assert r.json()["detail"]["codigo"] in ("PDF_CORRUPTO", "ARCHIVO_VACIO")

    def test_un_archivo_que_supera_el_limite_es_rechazado(self, api):
        """Valor límite superior: un byte por encima del máximo admitido."""
        contenido = CABECERA_PDF + b"\0" * (LIMITE_MB * 1024 * 1024 - len(CABECERA_PDF) + 1)
        r = api.post(
            "/api/v1/ingesta/cargar",
            files={"archivo": ("tesis.pdf", contenido, "application/pdf")},
            descripcion_entrada=f"archivo=tesis.pdf (<{len(contenido)} bytes> = límite + 1)",
        )
        assert r.status_code == 422
        assert r.json()["detail"]["codigo"] == "ARCHIVO_DEMASIADO_GRANDE"
        assert str(LIMITE_MB) in r.json()["detail"]["mensaje"]

    def test_en_el_limite_exacto_el_archivo_se_acepta(self, api, pdf_en_el_limite):
        """Valor límite: exactamente el máximo admitido. El límite es inclusivo."""
        r = api.post(
            "/api/v1/ingesta/cargar",
            files={"archivo": ("limite.pdf", pdf_en_el_limite, "application/pdf")},
            descripcion_entrada=f"archivo=limite.pdf (<{len(pdf_en_el_limite)} bytes> = límite exacto)",
        )
        assert r.status_code == 200
        assert r.json()["tamano_bytes"] == LIMITE_MB * 1024 * 1024

    def test_un_pdf_con_cabecera_valida_pero_cuerpo_danado_es_rechazado(self, api):
        """
        Un PDF truncado o dañado (cabecera correcta, contenido ilegible) es el
        caso más frecuente en la práctica: una descarga interrumpida, una
        exportación a medias. Debe rechazarse con 422 y código PDF_CORRUPTO,
        igual que cualquier otro archivo no procesable.
        """
        contenido = CABECERA_PDF + b"\0" * 4096
        r = api.post(
            "/api/v1/ingesta/cargar",
            files={"archivo": ("danado.pdf", contenido, "application/pdf")},
            descripcion_entrada=f"archivo=danado.pdf (<{len(contenido)} bytes>, cabecera %PDF y cuerpo ilegible)",
        )
        assert r.status_code == 422, f"Se obtuvo {r.status_code} en lugar de 422."
        assert r.json()["detail"]["codigo"] == "PDF_CORRUPTO"


class TestDocumentoInexistente:
    """Todo recurso pedido sobre un documento que no existe da un error explícito."""

    @pytest.mark.parametrize(
        "ruta, codigo",
        [
            ("/api/v1/ingesta/{d}/markdown", "MARKDOWN_NO_ENCONTRADO"),
            ("/api/v1/ingesta/{d}/pdf", "DOCUMENTO_NO_ENCONTRADO"),
            ("/api/v1/ingesta/{d}/estructura", "DOCUMENTO_NO_ENCONTRADO"),
            ("/api/v1/grafo/{d}/referencias", "REFERENCIAS_NO_ENCONTRADAS"),
            ("/api/v1/auditoria/{d}/veredictos", "VEREDICTOS_NO_ENCONTRADOS"),
            ("/api/v1/auditoria/{d}/metricas", "METRICAS_NO_ENCONTRADAS"),
        ],
    )
    def test_devuelve_404_con_codigo_de_error_propio(self, api, documento_inexistente, ruta, codigo):
        r = api.get(ruta.format(d=documento_inexistente))
        assert r.status_code == 404
        assert r.json()["detail"]["codigo"] == codigo
        assert r.json()["detail"]["accion_sugerida"]

    def test_las_colecciones_vacias_no_son_un_error(self, api, documento_inexistente):
        """Pedir las citas de un documento sin citas devuelve una colección vacía, no un 404."""
        r = api.get(f"/api/v1/grafo/{documento_inexistente}/citas")
        assert r.status_code == 200
        assert r.json()["total_citas"] == 0

    def test_un_identificador_con_inyeccion_no_rompe_el_servicio(self, api):
        """El identificador se trata como dato, no como consulta: ni 500 ni fuga de datos."""
        malicioso = "x' OR 1=1 RETURN n //"
        r = api.get(f"/api/v1/grafo/{malicioso}/citas")
        assert r.status_code < 500
        assert "cita" not in r.text.lower()

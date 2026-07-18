"""
Flujo del documento y máquina de estados.

Se carga el documento de prueba real y se recorre el ciclo de vida por la API:
PROCESANDO → REVISION_PENDIENTE → LISTO_EXTRACCION. Además de las transiciones
válidas, se comprueban las inválidas, que son las que un usuario provoca sin
querer y las que no deben producir nunca un fallo interno.

La auditoría completa no se ejecuta aquí: se comprueban sus guardas, que es lo
que corresponde a este nivel de prueba.
"""
import json


class TestCicloDeVidaDelDocumento:

    def test_la_carga_devuelve_identificador_paginas_y_estado_inicial(self, api, documento):
        c = documento["_carga"]
        api.anotar(c["peticion"], c["cuerpo_enviado"], c["estado"], c["cuerpo_recibido"])
        assert documento["paginas"] == 67
        assert documento["estado"] == "procesando"
        assert documento["documento_id"]

    def test_el_stream_de_progreso_recorre_el_pipeline_hasta_la_revision(self, api, documento):
        eventos = documento["_eventos"]
        api.anotar(
            f"GET /api/v1/ingesta/{documento['documento_id']}/progreso  (stream SSE)",
            "(sin cuerpo)",
            "200 OK  text/event-stream",
            f"{len(eventos)} eventos en {documento['_segundos']} s. Último:\n"
            + json.dumps(eventos[-1], ensure_ascii=False, indent=1),
        )
        assert [e["estado"] for e in eventos][-1] == "revision_pendiente"
        assert [e["porcentaje"] for e in eventos] == sorted(e["porcentaje"] for e in eventos)

    def test_la_estructura_detectada_incluye_la_seccion_de_referencias(self, api, documento):
        r = api.get(f"/api/v1/ingesta/{documento['documento_id']}/estructura")
        assert r.status_code == 200
        assert r.json()["tiene_seccion_referencias"] is True

    def test_el_markdown_extraido_queda_disponible(self, api, documento):
        r = api.get(f"/api/v1/ingesta/{documento['documento_id']}/markdown")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert "GraphRAG" in r.text

    def test_el_pdf_original_se_recupera(self, api, documento, pdf_tesis):
        r = api.get(f"/api/v1/ingesta/{documento['documento_id']}/pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert len(r.content) == len(pdf_tesis)

    def test_la_extraccion_encontro_las_citas_del_documento(self, api, documento):
        r = api.get(f"/api/v1/grafo/{documento['documento_id']}/citas")
        assert r.status_code == 200
        assert r.json()["total_citas"] > 0

    def test_la_extraccion_encontro_las_referencias_del_documento(self, api, documento):
        r = api.get(f"/api/v1/grafo/{documento['documento_id']}/referencias")
        assert r.status_code == 200
        assert r.json()["total_referencias"] > 0

    def test_el_resumen_del_grafo_concuerda_con_las_citas_y_referencias(self, api, documento):
        """El grafo que se muestra al usuario y las listas que consulta cuentan lo mismo."""
        doc = documento["documento_id"]
        citas = api.get(f"/api/v1/grafo/{doc}/citas").json()["total_citas"]
        refs = api.get(f"/api/v1/grafo/{doc}/referencias").json()["total_referencias"]
        r = api.get(f"/api/v1/grafo/{doc}/resumen")
        assert r.status_code == 200
        assert r.json()["nodos_citas"] == citas
        assert r.json()["nodos_referencias"] == refs


class TestTransicionesInvalidas:
    """Lo que el sistema debe impedir. El orden de estas pruebas es el del ciclo de vida."""

    def test_verificar_antes_de_confirmar_la_revision_es_rechazado(self, api, documento):
        r = api.post(
            f"/api/v1/ingesta/{documento['documento_id']}/verificar",
            json={"referencia_ids": ["r1"]},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["codigo"] == "ESTADO_INVALIDO"

    def test_confirmar_la_revision_desbloquea_la_verificacion(self, api, documento):
        r = api.post(f"/api/v1/ingesta/{documento['documento_id']}/confirmar-revision")
        assert r.status_code == 200
        assert r.json()["estado"] == "listo_extraccion"

    def test_confirmar_la_revision_dos_veces_es_rechazado(self, api, documento):
        r = api.post(f"/api/v1/ingesta/{documento['documento_id']}/confirmar-revision")
        assert r.status_code == 400
        assert r.json()["detail"]["codigo"] == "ESTADO_INVALIDO"

    def test_verificar_sin_seleccionar_referencias_es_rechazado(self, api, documento):
        r = api.post(
            f"/api/v1/ingesta/{documento['documento_id']}/verificar",
            json={"referencia_ids": []},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["codigo"] == "SIN_REFERENCIAS"

    def test_confirmar_la_revision_de_un_documento_inexistente_es_rechazado(self, api, documento_inexistente):
        r = api.post(f"/api/v1/ingesta/{documento_inexistente}/confirmar-revision")
        assert r.status_code == 400
        assert r.json()["detail"]["codigo"] == "ESTADO_INVALIDO"

    def test_los_veredictos_no_existen_antes_de_auditar(self, api, documento):
        r = api.get(f"/api/v1/auditoria/{documento['documento_id']}/veredictos")
        assert r.status_code == 404
        assert r.json()["detail"]["codigo"] == "VEREDICTOS_NO_ENCONTRADOS"

    def test_auditar_un_documento_sin_citas_no_produce_veredictos(self, api, documento_inexistente):
        r = api.post(f"/api/v1/auditoria/{documento_inexistente}/auditar")
        assert r.status_code == 404
        assert r.json()["detail"]["codigo"] == "SIN_CITAS"

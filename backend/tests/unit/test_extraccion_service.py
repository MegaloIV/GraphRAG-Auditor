"""
EntidadExtractionService — detección de citas y delimitación de secciones.

Todo se prueba sobre el texto real de la tesis. Las funciones que llaman al LLM
(extraer_citas / extraer_referencias) no se tocan aquí: lo que se prueba es la
lógica determinista que decide QUÉ texto se le manda al LLM y cómo se limpia lo
que devuelve.
"""
import re

from app.models.grafo import CitaEnTexto, TipoCita


class TestDetectarCitasRegex:
    """Prefiltro por regex: es lo que ancla la detección antes del LLM."""

    def test_encuentra_citas_en_la_tesis_real(self, svc, texto_tesis):
        citas = svc._detectar_citas_regex(texto_tesis)
        assert len(citas) >= 30

    def test_detecta_una_parentetica_con_et_al(self, svc, texto_tesis):
        citas = svc._detectar_citas_regex(texto_tesis)
        assert "(Edge et al., 2024)" in citas

    def test_detecta_una_parentetica_de_dos_autores_con_y(self, svc, texto_tesis):
        citas = svc._detectar_citas_regex(texto_tesis)
        assert "(Klesel y Wittmann, 2025)" in citas

    def test_todas_las_citas_llevan_un_anio_valido(self, svc, texto_tesis):
        citas = svc._detectar_citas_regex(texto_tesis)
        assert all(re.search(r"\b(19|20)\d{2}\b", c) for c in citas)

    def test_no_devuelve_subcoincidencias_de_otra_cita(self, svc, texto_tesis):
        citas = svc._detectar_citas_regex(texto_tesis)
        for cita in citas:
            assert not any(cita != otra and cita in otra for otra in citas)

    def test_un_texto_sin_citas_no_devuelve_nada(self, svc):
        assert svc._detectar_citas_regex("Un párrafo cualquiera sin ninguna cita APA.") == []


class TestSeccionDeReferencias:
    """Delimitación de la bibliografía dentro del documento."""

    def test_encuentra_el_encabezado_de_referencias(self, svc, texto_tesis):
        match = svc._encontrar_inicio_referencias(texto_tesis)
        assert match is not None
        assert "Referencias bibliográficas" in match.group()

    def test_el_encabezado_esta_en_la_parte_final_del_documento(self, svc, texto_tesis):
        match = svc._encontrar_inicio_referencias(texto_tesis)
        assert match.start() > len(texto_tesis) * 0.5

    def test_un_texto_sin_bibliografia_no_devuelve_encabezado(self, svc):
        assert svc._encontrar_inicio_referencias("# Introducción\n\nTexto del cuerpo.") is None

    def test_la_seccion_contiene_las_entradas_bibliograficas(self, seccion_referencias):
        assert "Ansari, S. (2026)" in seccion_referencias
        assert len(seccion_referencias) > 10_000

    def test_la_seccion_contiene_los_doi_de_las_fuentes(self, seccion_referencias):
        dois = re.findall(r"10\.\d{4,9}/\S+", seccion_referencias)
        assert len(dois) >= 30


class TestTruncarAntesDeAnexos:
    """Los anexos no se auditan: se recortan antes de detectar nada."""

    def test_recorta_los_anexos_de_la_tesis(self, svc, texto_tesis, texto_cuerpo):
        assert len(texto_cuerpo) < len(texto_tesis)

    def test_el_cuerpo_es_un_prefijo_exacto_del_documento(self, texto_tesis, texto_cuerpo):
        assert texto_tesis.startswith(texto_cuerpo)

    def test_conserva_la_bibliografia_que_precede_a_los_anexos(self, texto_cuerpo):
        assert "Referencias bibliográficas" in texto_cuerpo

    def test_un_texto_sin_anexos_no_se_recorta(self, svc):
        texto = "# Introducción\n\nCuerpo del documento sin apéndices."
        assert svc._truncar_antes_de_anexos(texto) == texto


class TestIndiceYSeccionesNoCitables:
    """El índice del propio texto sirve de mapa para descartar secciones."""

    def test_parsea_el_indice_de_la_tesis(self, svc, texto_tesis):
        entradas = svc._titulos_desde_indice(texto_tesis)
        assert len(entradas) >= 40

    def test_el_nivel_se_infiere_de_la_numeracion(self, svc, texto_tesis):
        entradas = dict((titulo, nivel) for nivel, titulo in svc._titulos_desde_indice(texto_tesis))
        assert entradas["1. Introducción"] == 1
        assert entradas["1.1. Contexto y antecedentes"] == 2

    def test_descarta_el_marco_metodologico(self, svc, texto_tesis):
        recortado = svc._recortar_secciones_no_citables(texto_tesis, None)
        assert len(recortado) < len(texto_tesis)

    def test_sin_indice_el_texto_se_devuelve_intacto(self, svc):
        texto = "# Introducción\n\nCuerpo sin tabla de contenido."
        assert svc._recortar_secciones_no_citables(texto, None) == texto


class TestDividirEnBloques:
    """El cuerpo se trocea para caber en la ventana del LLM."""

    def test_trocea_el_cuerpo_de_la_tesis(self, svc, texto_cuerpo):
        bloques = svc._dividir_en_bloques(texto_cuerpo)
        assert len(bloques) > 1

    def test_ningun_bloque_supera_el_maximo(self, svc, texto_cuerpo):
        bloques = svc._dividir_en_bloques(texto_cuerpo)
        assert all(len(b) <= 7000 for b in bloques)

    def test_no_se_generan_bloques_vacios(self, svc, texto_cuerpo):
        bloques = svc._dividir_en_bloques(texto_cuerpo)
        assert all(b.strip() for b in bloques)

    def test_las_citas_sobreviven_al_troceado(self, svc, texto_cuerpo):
        bloques = svc._dividir_en_bloques(texto_cuerpo)
        assert any("(Edge et al., 2024)" in b for b in bloques)


class TestDeduplicarCitas:
    """La misma ocurrencia extraída dos veces se colapsa; dos ocurrencias reales no."""

    def _cita(self, texto: str, fragmento: str, cita_id: str = "c1") -> CitaEnTexto:
        return CitaEnTexto(
            cita_id=cita_id,
            texto_cita=texto,
            tipo=TipoCita.PARENTETICA,
            pagina=1,
            fragmento_oracion=fragmento,
        )

    def test_colapsa_fragmentos_contenidos_y_conserva_el_mas_corto(self, svc):
        corto = "GraphRAG mejora la recuperación (Edge et al., 2024)."
        largo = "En trabajos recientes se observa que GraphRAG mejora la recuperación (Edge et al., 2024)."
        dedup = svc._deduplicar_citas([
            self._cita("(Edge et al., 2024)", largo, "c1"),
            self._cita("(Edge et al., 2024)", corto, "c2"),
        ])
        assert len(dedup) == 1
        assert dedup[0].fragmento_oracion == corto

    def test_conserva_la_misma_cita_en_parrafos_distintos(self, svc):
        dedup = svc._deduplicar_citas([
            self._cita("(Edge et al., 2024)", "El grafo estructura el corpus.", "c1"),
            self._cita("(Edge et al., 2024)", "La evaluación se hizo sobre preguntas globales.", "c2"),
        ])
        assert len(dedup) == 2

    def test_citas_distintas_nunca_se_colapsan(self, svc):
        dedup = svc._deduplicar_citas([
            self._cita("(Edge et al., 2024)", "Un fragmento.", "c1"),
            self._cita("(Fan et al., 2024)", "Un fragmento.", "c2"),
        ])
        assert len(dedup) == 2


class TestExtraerOracionFallback:
    """Cuando el LLM no devuelve la aseveración, se recorta del propio texto."""

    def test_recupera_la_oracion_que_rodea_a_la_cita(self, svc, texto_cuerpo):
        cita = "(Edge et al., 2024)"
        bloque = next(b for b in svc._dividir_en_bloques(texto_cuerpo) if cita in b)
        fragmento = svc._extraer_oracion_fallback(bloque, cita)
        assert cita in fragmento
        assert fragmento.endswith(cita)
        assert len(fragmento) <= 800

    def test_si_la_cita_no_esta_en_el_bloque_devuelve_vacio(self, svc, texto_cuerpo):
        bloque = svc._dividir_en_bloques(texto_cuerpo)[0]
        assert svc._extraer_oracion_fallback(bloque, "(Inexistente, 1999)") == ""


class TestParsearJSON:
    """Respuestas del LLM: JSONL, array y basura."""

    def test_parsea_una_respuesta_jsonl(self, svc):
        respuesta = (
            '{"autores": ["Ansari, S."], "anio": 2026, "titulo": "Compound Deception in Elite Peer Review"}\n'
            '{"autores": ["Edge, D."], "anio": 2024, "titulo": "From Local to Global"}'
        )
        objetos = svc._parsear_json(respuesta)
        assert len(objetos) == 2
        assert objetos[0]["anio"] == 2026

    def test_parsea_un_array_envuelto_en_markdown(self, svc):
        respuesta = '```json\n[{"texto_cita": "(Edge et al., 2024)", "tipo": "PARENTETICA"}]\n```'
        objetos = svc._parsear_json(respuesta)
        assert len(objetos) == 1
        assert objetos[0]["tipo"] == "PARENTETICA"

    def test_una_respuesta_sin_json_no_rompe(self, svc):
        assert svc._parsear_json("No he encontrado ninguna cita.") == []

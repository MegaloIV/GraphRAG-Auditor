"""
EntidadExtractionService — detección de citas y delimitación de secciones.

La detección de citas es determinista (sin LLM): se prueba sobre el texto real
de la tesis. Solo extraer_referencias sigue llamando al LLM y no se toca aquí.
La clase de regresiones usa textos mínimos, porque recoge formas halladas en
otras tesis que el PDF de prueba no contiene.
"""
import re

from app.models.grafo import CitaEnTexto, TipoCita
from app.services.grafo.extraccion_service import (
    _oracion_de,
    detectar_citas_estructuradas,
)


class TestDetectarCitas:
    """Detección determinista de citas: es la fuente de verdad del pipeline."""

    def test_encuentra_citas_en_la_tesis_real(self, svc, texto_tesis):
        citas = detectar_citas_estructuradas(texto_tesis)
        assert len({c.texto for c in citas}) >= 30

    def test_detecta_una_parentetica_con_et_al(self, svc, texto_tesis):
        textos = {c.texto for c in detectar_citas_estructuradas(texto_tesis)}
        assert "(Edge et al., 2024)" in textos

    def test_detecta_una_parentetica_de_dos_autores_con_y(self, svc, texto_tesis):
        textos = {c.texto for c in detectar_citas_estructuradas(texto_tesis)}
        assert "(Klesel y Wittmann, 2025)" in textos

    def test_todas_las_citas_llevan_un_anio_valido(self, svc, texto_tesis):
        citas = detectar_citas_estructuradas(texto_tesis)
        assert all(re.search(r"\b(19|20)\d{2}\b|s\.\s?f\.|n\.\s?d\.", c.texto) for c in citas)

    def test_el_texto_canonico_nunca_trae_saltos_de_linea(self, svc, texto_tesis):
        # El markdown del PDF parte las citas; el texto guardado debe salir limpio
        # porque de él dependen la vinculación y la búsqueda en el PDF.
        citas = detectar_citas_estructuradas(texto_tesis)
        assert all("\n" not in c.texto and "  " not in c.texto for c in citas)

    def test_un_texto_sin_citas_no_devuelve_nada(self, svc):
        assert detectar_citas_estructuradas("Un párrafo cualquiera sin ninguna cita APA.") == []


class TestDetectarCitasRegresiones:
    """
    Formas que el detector anterior perdía. Cada caso viene de una tesis real
    revisada durante el desarrollo; se reproducen con el texto mínimo porque el
    PDF de prueba no contiene todas las variantes.
    """

    def test_una_cita_partida_por_un_salto_de_linea(self):
        citas = detectar_citas_estructuradas("El modelo (LeCun et\nal., 2015) lo demuestra.")
        assert [c.texto for c in citas] == ["(LeCun et al., 2015)"]

    def test_un_grupo_produce_una_cita_por_fuente(self):
        citas = detectar_citas_estructuradas("Varios (Ahnlide et al., 2016; Unlu et al., 2014).")
        assert [c.texto for c in citas] == ["(Ahnlide et al., 2016)", "(Unlu et al., 2014)"]
        # Ambas comparten el texto literal: es lo que hay que buscar en el PDF.
        assert {c.original for c in citas} == {"(Ahnlide et al., 2016; Unlu et al., 2014)"}

    def test_apellidos_compuestos(self):
        citas = detectar_citas_estructuradas("Como (Hernández Sampieri & Mendoza Torres, 2018).")
        assert [c.texto for c in citas] == ["(Hernández Sampieri & Mendoza Torres, 2018)"]

    def test_autor_institucional(self):
        citas = detectar_citas_estructuradas("Según (American Cancer Society, 2018) el riesgo sube.")
        assert [c.texto for c in citas] == ["(American Cancer Society, 2018)"]

    def test_apellido_con_guion_y_caracteres_no_espanoles(self):
        textos = {c.texto for c in detectar_citas_estructuradas(
            "Ver (Johnson-Laird, 2006) y (Vrbančič & Podgorelec, 2020)."
        )}
        assert textos == {"(Johnson-Laird, 2006)", "(Vrbančič & Podgorelec, 2020)"}

    def test_el_conector_inicial_no_se_toma_como_apellido(self):
        citas = detectar_citas_estructuradas("Asimismo, Joshi et al (2022) lo resaltan.")
        assert [c.texto for c in citas] == ["Joshi et al (2022)"]

    def test_la_prosa_previa_no_se_toma_como_apellido(self):
        citas = detectar_citas_estructuradas("Según los resultados Smith (2020) mejora.")
        assert [c.texto for c in citas] == ["Smith (2020)"]


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


class TestOracionDe:
    """La afirmación del tesista que se manda al juez sale del span de la cita."""

    def test_la_afirmacion_precede_a_una_cita_parentetica(self):
        t = "Otra cosa. El modelo alcanza 94.2% de exactitud (Smith et al., 2020). Sigue."
        i, f = t.index("(Smith"), t.index("2020)") + 5
        assert _oracion_de(t, i, f, hasta_el_final=False) == (
            "El modelo alcanza 94.2% de exactitud (Smith et al., 2020)"
        )

    def test_la_afirmacion_sigue_a_la_cita_cuando_esta_abre_la_oracion(self):
        # "Según (Autor, año), ..." — cortar antes del paréntesis dejaba un
        # claim vacío y el juez no tenía nada que verificar.
        t = "Nada antes. Según (Yusuf et al., 2025), la IA mejora la retención. Fin."
        i, f = t.index("(Yusuf"), t.index("2025)") + 5
        assert _oracion_de(t, i, f, hasta_el_final=False).startswith("Según (Yusuf et al., 2025)")
        assert "retención" in _oracion_de(t, i, f, hasta_el_final=False)

    def test_una_cita_narrativa_arrastra_la_oracion_completa(self):
        t = "Antes nada. Smith (2020) sostiene que el método mejora 6.7 veces. Fin."
        i, f = t.index("Smith"), t.index("(2020)") + 6
        assert _oracion_de(t, i, f, hasta_el_final=True) == (
            "Smith (2020) sostiene que el método mejora 6.7 veces."
        )

    def test_una_inicial_no_corta_la_oracion(self):
        t = "Como indica Westpha E. (2021) el uso crece sin pausa. Fin."
        i, f = t.index("Westpha"), t.index("(2021)") + 6
        assert "el uso crece" in _oracion_de(t, i, f, hasta_el_final=True)

    def test_sin_puntuacion_alrededor_se_toma_una_ventana_corta(self):
        # Tablas y listados no tienen final de oración: antes se arrastraban
        # 700 caracteres de contenido ajeno al claim.
        t = "col1 col2 col3 " * 60 + "dato (Vargas, 2024) mas celdas"
        i, f = t.index("(Vargas"), t.index("2024)") + 5
        assert len(_oracion_de(t, i, f, hasta_el_final=False)) <= 300


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

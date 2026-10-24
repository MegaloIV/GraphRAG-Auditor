"""
limpiar_fragmento — saneo del markdown extraído del PDF.

Es la función que garantiza que el índice vectorial, el juez LLM, las cartillas
y el Excel vean exactamente el mismo texto corrido. Se prueba con fragmentos
tomados del markdown real de la tesis.
"""
from app.core.texto import aplanar, limpiar_fragmento


class TestLimpiarFragmento:

    def test_quita_las_negritas_del_markdown(self):
        assert limpiar_fragmento("**Jurado evaluador:**") == "Jurado evaluador:"

    def test_quita_los_encabezados(self):
        assert limpiar_fragmento("## 7. Referencias bibliográficas") == "7. Referencias bibliográficas"

    def test_colapsa_los_saltos_de_linea_en_texto_corrido(self):
        assert limpiar_fragmento("Presidente:\n\nSecretario:\n\nVocal:") == "Presidente: Secretario: Vocal:"

    def test_elimina_los_separadores_horizontales(self):
        assert "-----" not in limpiar_fragmento("Texto\n\n-----\n\nMás texto")

    def test_elimina_los_caracteres_invisibles_del_pdf(self):
        assert limpiar_fragmento("Graph​RAG­ Auditor") == "GraphRAG Auditor"

    def test_una_cadena_vacia_no_rompe(self):
        assert limpiar_fragmento("") == ""
        assert limpiar_fragmento(None) == ""

    def test_el_markdown_real_de_la_tesis_queda_en_una_sola_linea(self, texto_tesis):
        crudo = texto_tesis[texto_tesis.find("**"):][:400]
        limpio = limpiar_fragmento(crudo)
        assert "\n" not in limpio
        assert "**" not in limpio
        assert "  " not in limpio  # sin espacios dobles

    def test_no_inventa_ni_pierde_palabras(self):
        crudo = "**GraphRAG** mejora\nla recuperación"
        assert limpiar_fragmento(crudo) == "GraphRAG mejora la recuperación"


class TestAplanar:
    """
    Aplanado con mapa posicional. De él dependen la detección de citas y la
    localización en el PDF: si el mapa se desalinea, el resaltado apunta mal.
    """

    def test_colapsa_los_saltos_dentro_de_una_cita(self):
        assert aplanar("(LeCun et\n\nal., 2015)").plano == "(LeCun et al., 2015)"

    def test_une_la_palabra_cortada_con_guion_al_final_del_renglon(self):
        assert aplanar("Johnson-\nLaird").plano == "Johnson-Laird"

    def test_quita_el_marcado_markdown(self):
        assert aplanar("**Smith** (2020)").plano == "Smith (2020)"

    def test_descarta_las_reglas_de_tabla(self):
        assert aplanar("dato ----- otro").plano == "dato otro"

    def test_une_la_cita_partida_por_enlaces_de_zotero(self):
        # El plugin de Zotero para Google Docs envuelve cada cita en un enlace
        # y el PDF la parte en dos; sin quitar la sintaxis no se detecta.
        original = ("fraudes [(Alaghbari et al.,](https://www.zotero.org/google-docs/?OLyjvm) "
                    "[2023).](https://www.zotero.org/google-docs/?OLyjvm) Además")
        assert aplanar(original).plano == "fraudes (Alaghbari et al., 2023). Además"

    def test_no_toca_los_corchetes_que_no_son_enlaces(self):
        assert aplanar("como indica [1] y (Pérez, 2020)").plano == "como indica [1] y (Pérez, 2020)"

    def test_el_span_vuelve_a_las_coordenadas_del_original(self):
        original = "El modelo (LeCun et\n\nal., 2015) lo prueba."
        tp = aplanar(original)
        i = tp.plano.index("(LeCun")
        f = tp.plano.index(")") + 1
        ini, fin = tp.a_original(i, f)
        assert original[ini:fin] == "(LeCun et\n\nal., 2015)"

    def test_cada_caracter_del_plano_tiene_origen(self):
        tp = aplanar("  Hola\n\n mundo  **raro**  ")
        assert len(tp._mapa) == len(tp.plano)
        assert all(0 <= p < len(tp.original) for p in tp._mapa)

    def test_una_cadena_vacia_no_rompe(self):
        assert aplanar("").plano == ""
        assert aplanar(None).plano == ""

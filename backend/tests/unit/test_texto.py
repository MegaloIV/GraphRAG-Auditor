"""
limpiar_fragmento — saneo del markdown extraído del PDF.

Es la función que garantiza que el índice vectorial, el juez LLM, las cartillas
y el Excel vean exactamente el mismo texto corrido. Se prueba con fragmentos
tomados del markdown real de la tesis.
"""
from app.core.texto import limpiar_fragmento


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

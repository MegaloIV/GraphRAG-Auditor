"""
metricas_service — Kappa de Cohen, F1 y matriz de confusión.

Es scikit-learn puro sobre pares (predicción del sistema, etiqueta del experto).
Sin LLM y sin RAGAS. Aquí no interviene la tesis: lo que se prueba es la
aritmética que sustenta la vista /admin/evaluacion.
"""
import pytest

from app.services.evaluacion.metricas_service import LABELS, calcular_metricas


class TestAcuerdoPerfecto:

    def test_kappa_uno_cuando_sistema_y_experto_coinciden_en_todo(self):
        pares = [("SUPPORTS", "SUPPORTS"), ("REFUTES", "REFUTES"), ("NO_INFO", "NO_INFO")]
        r = calcular_metricas(pares)
        assert r["kappa_cohen"] == 1.0
        assert r["aciertos"] == 3
        assert r["total_evaluadas"] == 3

    def test_f1_uno_en_acuerdo_perfecto(self):
        pares = [("SUPPORTS", "SUPPORTS"), ("REFUTES", "REFUTES"), ("NO_INFO", "NO_INFO")]
        r = calcular_metricas(pares)
        assert r["macro"]["f1"] == 1.0
        assert r["weighted"]["f1"] == 1.0


class TestDesacuerdo:

    def test_kappa_cero_cuando_solo_hay_una_clase_presente(self):
        # cohen_kappa_score devuelve NaN; el servicio debe normalizarlo a 0.0
        pares = [("SUPPORTS", "SUPPORTS"), ("SUPPORTS", "SUPPORTS")]
        assert calcular_metricas(pares)["kappa_cohen"] == 0.0

    def test_kappa_negativo_cuando_el_sistema_falla_sistematicamente(self):
        pares = [("SUPPORTS", "REFUTES"), ("REFUTES", "SUPPORTS")] * 3
        r = calcular_metricas(pares)
        assert r["kappa_cohen"] < 0
        assert r["aciertos"] == 0

    def test_cuenta_los_aciertos_reales(self):
        pares = [
            ("SUPPORTS", "SUPPORTS"),   # acierto
            ("SUPPORTS", "REFUTES"),    # fallo
            ("NO_INFO", "NO_INFO"),     # acierto
            ("REFUTES", "NO_INFO"),     # fallo
        ]
        r = calcular_metricas(pares)
        assert r["aciertos"] == 2
        assert 0 < r["kappa_cohen"] < 1


class TestMatrizDeConfusion:

    def test_la_matriz_es_de_tres_por_tres(self):
        r = calcular_metricas([("SUPPORTS", "SUPPORTS")])
        assert len(r["matriz"]) == 3
        assert all(len(fila) == 3 for fila in r["matriz"])
        assert r["labels"] == LABELS

    def test_las_filas_son_el_experto_y_las_columnas_el_sistema(self):
        # El experto dijo REFUTES y el sistema dijo NO_INFO: fila REFUTES, columna NO_INFO.
        r = calcular_metricas([("NO_INFO", "REFUTES")])
        fila_refutes = LABELS.index("REFUTES")
        col_no_info = LABELS.index("NO_INFO")
        assert r["matriz"][fila_refutes][col_no_info] == 1

    def test_la_suma_de_la_matriz_es_el_total_evaluado(self):
        pares = [("SUPPORTS", "SUPPORTS"), ("REFUTES", "NO_INFO"), ("NO_INFO", "NO_INFO")]
        r = calcular_metricas(pares)
        assert sum(sum(fila) for fila in r["matriz"]) == r["total_evaluadas"] == 3


class TestMetricasPorCategoria:

    def test_devuelve_las_tres_categorias_en_orden_fijo(self):
        r = calcular_metricas([("SUPPORTS", "SUPPORTS")])
        assert [c["categoria"] for c in r["por_categoria"]] == LABELS

    def test_el_soporte_es_el_numero_de_casos_del_experto(self):
        pares = [("SUPPORTS", "SUPPORTS"), ("REFUTES", "SUPPORTS"), ("NO_INFO", "NO_INFO")]
        r = calcular_metricas(pares)
        soporte = {c["categoria"]: c["soporte"] for c in r["por_categoria"]}
        assert soporte["SUPPORTS"] == 2   # el experto etiquetó dos como SUPPORTS
        assert soporte["NO_INFO"] == 1

    def test_una_categoria_sin_casos_no_lanza_division_por_cero(self):
        r = calcular_metricas([("SUPPORTS", "SUPPORTS")])
        refutes = next(c for c in r["por_categoria"] if c["categoria"] == "REFUTES")
        assert refutes["f1"] == 0.0
        assert refutes["soporte"] == 0


class TestValidacion:

    def test_sin_pares_lanza_error_explicito(self):
        with pytest.raises(ValueError):
            calcular_metricas([])

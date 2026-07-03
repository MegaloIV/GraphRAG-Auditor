"""
Métricas de evaluación contra ground truth experto (Kappa / F1 / matriz).

Compara la predicción del sistema (veredicto de cada Cita en Neo4j) contra la
etiqueta asignada por un experto. NO usa LLM ni RAGAS: es scikit-learn puro
sobre pares (prediccion_sistema, etiqueta_experto).
"""
import math

import structlog
from sklearn.metrics import (
    confusion_matrix,
    precision_recall_fscore_support,
    cohen_kappa_score,
)

logger = structlog.get_logger(__name__)

# Orden fijo de labels en matriz y métricas por categoría.
LABELS = ["SUPPORTS", "REFUTES", "NO_INFO"]


def calcular_metricas(pares: list[tuple[str, str]]) -> dict:
    """
    Calcula matriz de confusión 3×3, precisión/recall/F1 por categoría (+ macro
    y weighted) y Kappa de Cohen.

    Args:
        pares: [(prediccion_sistema, etiqueta_experto), ...] con labels en
               {"SUPPORTS", "REFUTES", "NO_INFO"}. Los no emparejados se
               excluyen ANTES de llamar aquí (los cuenta el router).

    Returns:
        dict con: total_evaluadas, aciertos, kappa_cohen,
        matriz (filas = verdadero/experto, columnas = predicho/sistema),
        por_categoria, macro, weighted.
    """
    if not pares:
        raise ValueError("Se requiere al menos un par (prediccion, etiqueta) para evaluar.")

    y_pred = [p for p, _ in pares]   # sistema
    y_true = [e for _, e in pares]   # experto

    matriz = confusion_matrix(y_true, y_pred, labels=LABELS)
    aciertos = int(matriz.trace())

    prec, rec, f1, soporte = precision_recall_fscore_support(
        y_true, y_pred, labels=LABELS, average=None, zero_division=0
    )
    por_categoria = [
        {
            "categoria": label,
            "precision": round(float(prec[i]), 3),
            "recall": round(float(rec[i]), 3),
            "f1": round(float(f1[i]), 3),
            "soporte": int(soporte[i]),
        }
        for i, label in enumerate(LABELS)
    ]

    agregados = {}
    for promedio in ("macro", "weighted"):
        p, r, f, _ = precision_recall_fscore_support(
            y_true, y_pred, labels=LABELS, average=promedio, zero_division=0
        )
        agregados[promedio] = {
            "categoria": promedio,
            "precision": round(float(p), 3),
            "recall": round(float(r), 3),
            "f1": round(float(f), 3),
            "soporte": len(pares),
        }

    # Con una sola clase presente, cohen_kappa_score devuelve NaN.
    kappa = cohen_kappa_score(y_true, y_pred, labels=LABELS)
    kappa = 0.0 if math.isnan(kappa) else round(float(kappa), 3)

    logger.info(
        "metricas_calculadas",
        total=len(pares),
        aciertos=aciertos,
        kappa=kappa,
    )
    return {
        "total_evaluadas": len(pares),
        "aciertos": aciertos,
        "kappa_cohen": kappa,
        "matriz": matriz.tolist(),
        "labels": LABELS,
        "por_categoria": por_categoria,
        "macro": agregados["macro"],
        "weighted": agregados["weighted"],
    }

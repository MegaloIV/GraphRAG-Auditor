"""
Modelos del módulo de evaluación contra ground truth experto (Kappa / F1).

Separado de RAGAS: aquí se compara el veredicto del sistema (SUPPORTS /
REFUTES / NO_INFO) contra la etiqueta asignada por un experto humano.
"""
from typing import Optional
from pydantic import BaseModel, Field

from app.models.auditoria import VeredictoTipo


class CategoriaMetricas(BaseModel):
    categoria: str          # "SUPPORTS" | "REFUTES" | "NO_INFO" | "macro" | "weighted"
    precision: float
    recall: float
    f1: float
    soporte: int


class MatrizConfusion(BaseModel):
    # filas = verdadero (experto), columnas = predicho (sistema)
    labels: list[str]
    filas: list[list[int]]


class EvaluacionResultado(BaseModel):
    documento_id: str
    total_evaluadas: int
    aciertos: int
    kappa_cohen: float
    matriz_confusion: MatrizConfusion
    por_categoria: list[CategoriaMetricas]
    macro: CategoriaMetricas
    weighted: CategoriaMetricas
    no_emparejadas_sistema: int    # citas con veredicto sin etiqueta experta
    no_emparejadas_experto: int    # etiquetas expertas sin cita del sistema
    evaluado_en: str               # ISO 8601


class EtiquetaExperto(BaseModel):
    cita_id: str
    etiqueta_experto: VeredictoTipo


class EvaluarRequest(BaseModel):
    # Fuente temporal del ground truth (D3): las etiquetas viajan en el body.
    # TODO(B3): sustituir por ground_truth_loader.cargar(documento_id) cuando
    # se defina el mecanismo de carga.
    etiquetas: list[EtiquetaExperto] = Field(min_length=1)

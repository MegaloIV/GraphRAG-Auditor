"""
Módulo de Coherencia Inter-Fuentes (diseño: docs/COHERENCIA.md).

Modelos de la API: hallazgos derivados del grafo de evidencias y el mapa de
referencias serializado para la visualización del frontend. Las relaciones
semánticas (APOYA/CONTRADICE/COMPLEMENTA) se juzgan entre fragmentos de los
papers (Evidencia), nunca entre las aseveraciones del tesista.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TipoHallazgo(str, Enum):
    CONTRADICCION_INTERNA = "contradiccion_interna"   # gravedad alta
    TRIANGULACION = "triangulacion"                   # positivo
    FUENTE_ISLA = "fuente_isla"                       # aviso
    CONCEPTO_DEBIL = "concepto_debil"                 # aviso


class HallazgoCoherencia(BaseModel):
    tipo: TipoHallazgo
    cita_ids: list[str] = Field(default_factory=list)
    referencia_ids: list[str] = Field(default_factory=list)
    referencias: list[str] = Field(default_factory=list)   # títulos, para la UI
    concepto: Optional[str] = None
    justificacion: str
    confianza: Optional[str] = None                        # "alta" | "media" (de la arista LLM)


class NodoMapa(BaseModel):
    """Nodo del mapa de referencias (proyección solo-referencias, §9.1)."""
    id: str                          # referencia_id
    label: str
    titulo: Optional[str] = None
    anio: Optional[int] = None
    n_citas: int = 0                 # citas del tesista que dependen del paper
    es_isla: bool = False


class DetalleRelacion(BaseModel):
    """Un par de evidencias juzgado, para el drill-down por arista (§9.2)."""
    evidencia_1: str                 # fragmento del paper source (recortado)
    evidencia_2: str                 # fragmento del paper target (recortado)
    justificacion: str
    confianza: str
    concepto: Optional[str] = None
    cita_ids: list[str] = Field(default_factory=list)


class AristaMapa(BaseModel):
    source: str                      # referencia_id
    target: str                      # referencia_id
    tipo: str                        # APOYA | CONTRADICE | COMPLEMENTA | CO_MENCION
    pares: int = 1                   # nº de pares de evidencias que la sustentan
    detalles: list[DetalleRelacion] = Field(default_factory=list)


class ConceptoResumen(BaseModel):
    id: str
    nombre: str
    n_referencias: int
    referencia_ids: list[str] = Field(default_factory=list)


class MapaCoherencia(BaseModel):
    nodes: list[NodoMapa] = Field(default_factory=list)
    links: list[AristaMapa] = Field(default_factory=list)
    conceptos: list[ConceptoResumen] = Field(default_factory=list)


class CoherenciaResponse(BaseModel):
    documento_id: str
    construido_en: Optional[str] = None      # ISO 8601
    total_evidencias: int = 0
    total_conceptos: int = 0
    total_comparaciones: int = 0             # pares enviados al juez
    total_relaciones: int = 0                # aristas APOYA/CONTRADICE/COMPLEMENTA
    hallazgos: list[HallazgoCoherencia] = Field(default_factory=list)
    mapa: MapaCoherencia = Field(default_factory=MapaCoherencia)

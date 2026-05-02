from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class TipoCita(str, Enum):
    PARENTETICA = "parentetica"    # (Apellido, año)
    NARRATIVA = "narrativa"        # Apellido (año)


# HU-004: Una referencia bibliográfica APA
class ReferenciaAPA(BaseModel):
    referencia_id: str
    autores: list[str]
    anio: Optional[int] = None
    titulo: str
    fuente: Optional[str] = None
    doi: Optional[str] = None
    datos_incompletos: bool = False
    campos_faltantes: list[str] = Field(default_factory=list)


# HU-004: Respuesta con todas las referencias del documento
class ReferenciasResponse(BaseModel):
    documento_id: str
    total_referencias: int
    referencias: list[ReferenciaAPA]
    referencias_incompletas: int
    advertencia: Optional[str] = None


# HU-005: Una cita encontrada en el cuerpo del texto
class CitaEnTexto(BaseModel):
    cita_id: str
    texto_cita: str
    tipo: TipoCita
    pagina: int
    fragmento_oracion: str
    referencia_id: Optional[str] = None


# HU-005: Respuesta con todas las citas del documento
class CitasResponse(BaseModel):
    documento_id: str
    total_citas: int
    citas_parenteticas: int
    citas_narrativas: int
    citas: list[CitaEnTexto]
    advertencia: Optional[str] = None


# HU-006: Resumen del grafo construido en Neo4j
class ResumenGrafo(BaseModel):
    documento_id: str
    total_nodos: int
    nodos_autores: int
    nodos_referencias: int
    nodos_citas: int
    total_relaciones: int
    densidad_promedio: float
    grafo_robusto: bool
    advertencia_densidad: Optional[str] = None
    error: Optional[str] = None
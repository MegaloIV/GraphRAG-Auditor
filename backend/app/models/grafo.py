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
    nivel_confianza: Optional[str] = None


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
    pagina: int = 0     
    fragmento_oracion: str = ""
    referencia_id: Optional[str] = None


# HU-005: Respuesta con todas las citas del documento
class CitasResponse(BaseModel):
    documento_id: str
    total_citas: int
    citas_parenteticas: int
    citas_narrativas: int
    citas: list[CitaEnTexto]
    advertencia: Optional[str] = None


# ── CRUD de revisión humana: requests de citas y referencias ─────────────────

class ActualizarCitaRequest(BaseModel):
    texto_cita: Optional[str] = None
    tipo: Optional[TipoCita] = None
    pagina: Optional[int] = None
    fragmento_oracion: Optional[str] = None
    # Permite re-vincular manualmente la cita a una referencia (o desvincular con "").
    referencia_id: Optional[str] = None


class CrearCitaRequest(BaseModel):
    texto_cita: str
    tipo: TipoCita
    pagina: int
    fragmento_oracion: str


class EliminarCitasRequest(BaseModel):
    """Eliminación en lote durante la revisión humana."""
    cita_ids: list[str]


class ActualizarReferenciaRequest(BaseModel):
    autores: Optional[list[str]] = None
    anio: Optional[int] = None
    titulo: Optional[str] = None
    fuente: Optional[str] = None
    doi: Optional[str] = None
    datos_incompletos: Optional[bool] = None
    campos_faltantes: Optional[list[str]] = None


class CrearReferenciaRequest(BaseModel):
    autores: list[str]
    anio: Optional[int] = None
    titulo: str
    fuente: Optional[str] = None
    doi: Optional[str] = None
    datos_incompletos: bool = False
    campos_faltantes: list[str] = Field(default_factory=list)


# ── Revisión humana: localización exacta de citas en el PDF (B1.2) ───────────

class RectResaltado(BaseModel):
    """Rectángulo donde aparece la cita, en coordenadas de página de PyMuPDF."""
    pagina: int                 # 1-based
    x0: float
    y0: float
    x1: float
    y1: float
    ancho_pagina: float         # dimensiones del page para escalar en el frontend
    alto_pagina: float


class UbicacionCita(BaseModel):
    cita_id: str
    texto_cita: str
    pagina_real: Optional[int] = None   # página 1-based del PDF; None si no se localizó
    rects: list[RectResaltado] = Field(default_factory=list)


class UbicacionesCitasResponse(BaseModel):
    documento_id: str
    total_citas: int
    localizadas: int
    pagina_referencias: Optional[int] = None   # página 1-based de la sección de referencias
    ubicaciones: list[UbicacionCita]


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
    citas_vinculadas: int = 0      
    advertencia_densidad: Optional[str] = None
    error: Optional[str] = None
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class VeredictoTipo(str, Enum):
    VALIDA = "VÁLIDA"
    DUDOSA = "DUDOSA"
    ALUCINADA = "ALUCINADA"
    NO_VERIFICABLE = "NO_VERIFICABLE"


# HU-010: Veredicto de una cita individual
class VeredictoAuditoria(BaseModel):
    cita_id: str
    texto_cita: str
    fragmento_oracion: str = ""
    pagina: int
    veredicto: VeredictoTipo
    justificacion: str
    fragmento_evidencia: str = ""
    similitud: float = 0.0
    referencia_id: Optional[str] = None
    titulo_referencia: Optional[str] = None
    doi_referencia: Optional[str] = None
    autores_referencia: list[str] = Field(default_factory=list)
    anio_referencia: Optional[int] = None
    metodo_recuperacion: str = ""
    pagina_paper: Optional[int] = None


# HU-010: Respuesta completa de auditoría del documento
class AuditoriaResponse(BaseModel):
    documento_id: str
    total_citas: int
    validas: int
    dudosas: int
    alucinadas: int
    no_verificables: int
    veredictos: list[VeredictoAuditoria]
    advertencia: Optional[str] = None


# HU-011: Una alerta de inconsistencia estructural
class AlertaInconsistencia(BaseModel):
    tipo: str          # "cita_sin_referencia" | "referencia_sin_citar"
    descripcion: str
    elemento: str      # texto de la cita o título de la referencia
    ubicacion: str     # página o "sección de referencias"


# HU-011: Respuesta de alertas estructurales
class AlertasResponse(BaseModel):
    documento_id: str
    citas_sin_referencia: list[AlertaInconsistencia]
    referencias_sin_citar: list[AlertaInconsistencia]
    total_inconsistencias: int
    mensaje: str


# HU-012: Alerta de alucinación del sistema
class AlertaAlucinacionSistema(BaseModel):
    cita_id: str
    texto_cita: str
    pagina: int
    razon_no_verificable: str


# HU-012: Respuesta de alertas de alucinación del sistema
class AlertasAlucinacionResponse(BaseModel):
    documento_id: str
    total_no_verificables: int
    alertas: list[AlertaAlucinacionSistema]
    advertencia: Optional[str] = None
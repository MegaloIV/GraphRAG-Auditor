from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class EstadoIngesta(str, Enum):
    PENDIENTE = "pendiente"
    PROCESANDO = "procesando"
    REVISION_PENDIENTE = "revision_pendiente"
    LISTO_EXTRACCION = "listo_extraccion"
    VERIFICANDO = "verificando"
    COMPLETADO = "completado"
    ERROR = "error"


class TipoSeccion(str, Enum):
    TITULO = "titulo"
    RESUMEN = "resumen"
    INTRODUCCION = "introduccion"
    CUERPO = "cuerpo"
    METODOLOGIA = "metodologia"
    RESULTADOS = "resultados"
    DISCUSION = "discusion"
    CONCLUSION = "conclusion"
    REFERENCIAS = "referencias"
    DESCONOCIDO = "desconocido"


# HU-001: Respuesta al cargar un PDF
class DocumentoCargadoResponse(BaseModel):
    documento_id: str
    nombre_archivo: str
    tamano_bytes: int
    paginas: int
    estado: EstadoIngesta
    mensaje: str


# HU-002: Cada sección detectada en el documento
class SeccionDetectada(BaseModel):
    tipo: TipoSeccion
    titulo_detectado: str
    pagina_inicio: int
    pagina_fin: int
    tiene_referencias: bool = False


# HU-002: Respuesta completa de estructura del documento
class EstructuraDocumentoResponse(BaseModel):
    documento_id: str
    total_paginas: int
    secciones: list[SeccionDetectada]
    tiene_seccion_referencias: bool
    advertencia: Optional[str] = None


# HU-003: Progreso de la auditoría en tiempo real
class ProgresoAuditoriaResponse(BaseModel):
    documento_id: str
    estado: EstadoIngesta
    porcentaje: int = Field(..., ge=0, le=100)
    mensaje_progreso: str
    citas_encontradas: Optional[int] = None
    error: Optional[str] = None


# HU-VER: Solicitud de verificación externa con referencias seleccionadas
class VerificacionSolicitud(BaseModel):
    referencia_ids: list[str]


# RN-002: Formato estándar de errores (sin detalles técnicos internos)
class ErrorResponse(BaseModel):
    codigo: str
    mensaje: str
    accion_sugerida: str
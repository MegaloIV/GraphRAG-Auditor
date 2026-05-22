from pathlib import Path
import structlog
from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.models.grafo import ReferenciasResponse, CitasResponse, ResumenGrafo, ReferenciaAPA, CitaEnTexto, TipoCita
from app.services.grafo.grafo_carga_service import grafo_carga_service
from app.services.grafo.neo4j_service import neo4j_service

logger = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter(prefix="/grafo", tags=["Grafo"])


@router.get(
    "/{documento_id}/referencias",
    response_model=ReferenciasResponse,
    summary="HU-004: Ver referencias bibliográficas identificadas",
)
async def ver_referencias(documento_id: str):
    """
    Lee las referencias desde Neo4j — no vuelve a llamar al LLM.
    """
    try:
        query = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
        OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
        RETURN r, collect(a.nombre) AS autores
        """
        referencias = []
        with neo4j_service.driver.session() as session:
            result = session.run(query, doc_id=documento_id)
            for record in result:
                r = record["r"]
                referencias.append(ReferenciaAPA(
                    referencia_id=r["id"],
                    autores=record["autores"],
                    anio=r.get("anio"),
                    titulo=r.get("titulo", ""),
                    fuente=r.get("fuente"),
                    doi=r.get("doi_verificado") or r.get("doi"),
                    datos_incompletos=r.get("datos_incompletos", False),
                    campos_faltantes=[],
                ))

        if not referencias:
            raise HTTPException(
                status_code=404,
                detail={
                    "codigo": "REFERENCIAS_NO_ENCONTRADAS",
                    "mensaje": "No se encontraron referencias para este documento.",
                    "accion_sugerida": "Verifica que la auditoría se haya completado.",
                },
            )

        incompletas = sum(1 for r in referencias if r.datos_incompletos)
        advertencia = None
        if incompletas > 0:
            advertencia = f"Se detectaron {incompletas} referencias con datos incompletos."

        return ReferenciasResponse(
            documento_id=documento_id,
            total_referencias=len(referencias),
            referencias=referencias,
            referencias_incompletas=incompletas,
            advertencia=advertencia,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("error_ver_referencias", doc_id=documento_id, error=str(e))
        raise HTTPException(status_code=500, detail={"codigo": "ERROR_INTERNO", "mensaje": str(e), "accion_sugerida": "Intenta nuevamente."})


@router.get(
    "/{documento_id}/citas",
    response_model=CitasResponse,
    summary="HU-005: Ver citas detectadas en el cuerpo del texto",
)
async def ver_citas(documento_id: str):
    """
    Lee las citas desde Neo4j — no vuelve a llamar al LLM.
    """
    try:
        query = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
        RETURN c
        """
        citas = []
        with neo4j_service.driver.session() as session:
            result = session.run(query, doc_id=documento_id)
            for record in result:
                c = record["c"]
                tipo = TipoCita.PARENTETICA if c.get("tipo") == "parentetica" else TipoCita.NARRATIVA
                citas.append(CitaEnTexto(
                    cita_id=c["id"],
                    texto_cita=c.get("texto", ""),
                    tipo=tipo,
                    pagina=c.get("pagina", 0),
                    fragmento_oracion=c.get("fragmento", ""),
                ))

        parenteticas = sum(1 for c in citas if c.tipo == TipoCita.PARENTETICA)
        narrativas = sum(1 for c in citas if c.tipo == TipoCita.NARRATIVA)

        advertencia = None
        if not citas:
            advertencia = "No se detectaron citas APA en el documento."

        return CitasResponse(
            documento_id=documento_id,
            total_citas=len(citas),
            citas_parenteticas=parenteticas,
            citas_narrativas=narrativas,
            citas=citas,
            advertencia=advertencia,
        )

    except Exception as e:
        logger.error("error_ver_citas", doc_id=documento_id, error=str(e))
        raise HTTPException(status_code=500, detail={"codigo": "ERROR_INTERNO", "mensaje": str(e), "accion_sugerida": "Intenta nuevamente."})


@router.get(
    "/{documento_id}/resumen",
    response_model=ResumenGrafo,
    summary="HU-006: Ver confirmación del grafo construido",
)
async def ver_resumen_grafo(documento_id: str):
    """
    Retorna el resumen del grafo de conocimiento construido.
    """
    try:
        return grafo_carga_service.obtener_resumen_grafo(documento_id)
    except Exception as e:
        logger.error("error_resumen_grafo", doc_id=documento_id, error=str(e))
        return ResumenGrafo(
            documento_id=documento_id,
            total_nodos=0,
            nodos_autores=0,
            nodos_referencias=0,
            nodos_citas=0,
            total_relaciones=0,
            densidad_promedio=0.0,
            grafo_robusto=False,
            error="No se pudo obtener el resumen del grafo.",
        )
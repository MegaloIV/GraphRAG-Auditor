import tempfile
from pathlib import Path
import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile

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
                    nivel_confianza=r.get("nivel_confianza"),
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
        OPTIONAL MATCH (c)-[:CITA_A]->(r:Referencia)
        RETURN c, r.id AS referencia_id
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
                    referencia_id=record["referencia_id"],
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


@router.get(
    "/{documento_id}/grafo-visual",
    summary="Nodos y relaciones del grafo para visualización interactiva",
)
async def ver_grafo_visual(documento_id: str):
    """
    Retorna nodes[] y links[] con el estado actual del grafo en Neo4j.
    Los nodos Referencia incluyen nivel_confianza para colorear por fase.
    """
    try:
        nodes: list[dict] = []
        links: list[dict] = []
        seen: set[str] = set()

        with neo4j_service.driver.session() as session:
            # Documento
            rec_doc = session.run(
                "MATCH (d:Documento {id: $id}) RETURN d", id=documento_id
            ).single()
            if not rec_doc:
                raise HTTPException(status_code=404, detail={
                    "codigo": "DOCUMENTO_NO_ENCONTRADO",
                    "mensaje": "Documento no encontrado.",
                    "accion_sugerida": "Verifica el ID.",
                })
            d = rec_doc["d"]
            nodes.append({"id": documento_id, "tipo": "Documento",
                          "label": d.get("nombre_archivo", "Documento")})
            seen.add(documento_id)

            # Referencias y sus autores
            for rec in session.run(
                """
                MATCH (d:Documento {id: $id})-[:TIENE_REFERENCIA]->(r:Referencia)
                OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
                RETURN r, collect(DISTINCT a) AS autores
                """,
                id=documento_id,
            ):
                r      = rec["r"]
                ref_id = r["id"]
                if ref_id not in seen:
                    autores_lista = [a["nombre"] for a in rec["autores"] if a]
                    apellido = autores_lista[0].split(",")[0] if autores_lista else "?"
                    anio     = r.get("anio") or ""
                    nodes.append({
                        "id":              ref_id,
                        "tipo":            "Referencia",
                        "label":           f"{apellido} ({anio})" if anio else apellido,
                        "titulo":          r.get("titulo", ""),
                        "nivel_confianza": r.get("nivel_confianza"),
                        "anio":            anio or None,
                        "fuente":          r.get("fuente"),
                        "doi":             r.get("doi_verificado") or r.get("doi"),
                        "score_crossref":  r.get("score_crossref"),
                        "autores":         autores_lista,
                    })
                    seen.add(ref_id)
                    links.append({"source": documento_id, "target": ref_id,
                                  "tipo": "TIENE_REFERENCIA"})

                for a in rec["autores"]:
                    if not a:
                        continue
                    autor_id = a["nombre_normalizado"]
                    if autor_id not in seen:
                        nodes.append({"id": autor_id, "tipo": "Autor",
                                      "label": a.get("nombre", autor_id)})
                        seen.add(autor_id)
                    links.append({"source": ref_id, "target": autor_id,
                                  "tipo": "ESCRITO_POR"})

            # Citas y su vínculo CITA_A
            for rec in session.run(
                """
                MATCH (d:Documento {id: $id})-[:TIENE_CITA]->(c:Cita)
                OPTIONAL MATCH (c)-[:CITA_A]->(r:Referencia)
                RETURN c, r.id AS ref_id
                """,
                id=documento_id,
            ):
                c       = rec["c"]
                cita_id = c["id"]
                if cita_id not in seen:
                    nodes.append({
                        "id":         cita_id,
                        "tipo":       "Cita",
                        "label":      c.get("texto", "Cita"),
                        "tipo_cita":  c.get("tipo", "parentetica"),
                        "pagina":     c.get("pagina"),
                        "fragmento":           c.get("fragmento"),
                        "veredicto":           c.get("veredicto"),
                        "justificacion":       c.get("justificacion"),
                        "fragmento_evidencia": c.get("fragmento_evidencia"),
                        "pagina_paper":        c.get("pagina_paper"),
                        "similitud":           c.get("similitud"),
                        "faithfulness":        c.get("faithfulness"),
                        "answer_relevancy":    c.get("answer_relevancy"),
                        "context_precision":   c.get("context_precision"),
                    })
                    seen.add(cita_id)
                    links.append({"source": documento_id, "target": cita_id,
                                  "tipo": "TIENE_CITA"})
                if rec["ref_id"]:
                    links.append({"source": cita_id, "target": rec["ref_id"],
                                  "tipo": "CITA_A"})

        return {"nodes": nodes, "links": links}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("error_grafo_visual", doc_id=documento_id, error=str(e))
        raise HTTPException(status_code=500, detail={
            "codigo": "ERROR_INTERNO",
            "mensaje": str(e),
            "accion_sugerida": "Intenta nuevamente.",
        })


@router.post(
    "/{documento_id}/referencias/{referencia_id}/paper",
    summary="HU-VER: Subir paper manualmente para una referencia no encontrada",
)
async def subir_paper_manual(
    documento_id: str,
    referencia_id: str,
    archivo: UploadFile = File(...),
):
    """
    Permite al usuario adjuntar el PDF de un paper cuando CrossRef/Unpaywall
    no lo encontraron automáticamente. El texto se extrae, se genera el embedding
    y se indexa en Supabase. El nodo Referencia se actualiza en Neo4j.
    """
    from app.services.externo.embedding_service import embedding_service
    import pymupdf4llm

    contenido = await archivo.read()
    if len(contenido) < 100:
        raise HTTPException(
            status_code=422,
            detail={"codigo": "ARCHIVO_INVALIDO", "mensaje": "El archivo parece estar vacío.", "accion_sugerida": "Sube un PDF válido."},
        )

    # Obtener metadatos de la referencia desde Neo4j
    try:
        query_ref = "MATCH (r:Referencia {id: $ref_id}) RETURN r"
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            result = session.run(query_ref, ref_id=referencia_id)
            record = result.single()
            if not record:
                raise HTTPException(status_code=404, detail={"codigo": "REFERENCIA_NO_ENCONTRADA", "mensaje": "No se encontró la referencia.", "accion_sugerida": "Verifica el ID de la referencia."})
            r = record["r"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"codigo": "ERROR_NEO4J", "mensaje": str(e), "accion_sugerida": "Intenta nuevamente."})

    # Guardar PDF en archivo temporal, extraer texto
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenido)
            tmp_path = tmp.name

        texto = pymupdf4llm.to_markdown(tmp_path)
        Path(tmp_path).unlink(missing_ok=True)

        if not texto or len(texto.strip()) < 100:
            raise HTTPException(
                status_code=422,
                detail={"codigo": "PDF_SIN_TEXTO", "mensaje": "No se pudo extraer texto del PDF.", "accion_sugerida": "Asegúrate de que el PDF no sea una imagen escaneada."},
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail={"codigo": "ERROR_EXTRACCION", "mensaje": f"Error al procesar el PDF: {str(e)}", "accion_sugerida": "Intenta con otro archivo."})

    # Indexar en Supabase con doi sintético basado en referencia_id
    doi_manual = f"manual_{referencia_id}"
    metadata = {
        "doi": doi_manual,
        "doi_normalizado": doi_manual.replace("/", "_").replace(".", "_"),
        "titulo": r.get("titulo", "Paper subido manualmente"),
        "anio": str(r.get("anio") or ""),
        "nivel_confianza": "manual",
        "referencia_id": referencia_id,
    }

    try:
        embedding_service.indexar_paper(doi=doi_manual, texto=texto, metadata=metadata)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"codigo": "ERROR_INDEXADO", "mensaje": f"Error al indexar el paper: {str(e)}", "accion_sugerida": "Intenta nuevamente."})

    # Actualizar Neo4j
    try:
        query_update = """
        MATCH (r:Referencia {id: $ref_id})
        SET r.nivel_confianza = 'manual',
            r.doi_verificado = $doi_manual,
            r.verificado = true,
            r.verificado_en = datetime()
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            session.run(query_update, ref_id=referencia_id, doi_manual=doi_manual)
    except Exception as e:
        logger.error("error_actualizar_neo4j_manual", ref_id=referencia_id, error=str(e))

    logger.info("paper_manual_indexado", ref_id=referencia_id, doi=doi_manual)
    return {"nivel_confianza": "manual", "doi": doi_manual, "mensaje": "Paper indexado correctamente."}
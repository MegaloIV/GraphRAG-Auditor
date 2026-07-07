"""
Servicio orquestador de verificación externa de referencias.
Une CrossRef + Unpaywall + Supabase/pgvector + Neo4j.

La verificación automática opera SOLO con el DOI de la referencia: la antigua
búsqueda en CrossRef por título/autor traía papers equivocados. Sin DOI, la
referencia queda 'no_encontrado' y se resuelve importando la colección de
Zotero (.ris/.zip) o subiendo el PDF manualmente.

Flujo por referencia (con DOI):
1. CrossRef GET /works/{doi} → título oficial + abstract (sin adivinar)
2. Unpaywall → ¿hay PDF gratuito? → texto completo
3. Supabase/pgvector  → generar y guardar embedding
4. Neo4j     → actualizar nodo Referencia con resultados
"""
import structlog

from app.services.externo.crossref_service import crossref_service
from app.services.externo.unpaywall_service import unpaywall_service
from app.services.externo.embedding_service import embedding_service
from app.services.externo.doi_utils import normalizar_doi, doi_para_chunks
from app.services.grafo.neo4j_service import neo4j_service
from app.models.grafo import ReferenciaAPA
from app.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class VerificacionService:

    def verificar_referencias(
        self,
        referencias: list[ReferenciaAPA],
        documento_id: str,
        max_referencias: int = 10,
    ) -> dict:
        """
        Verifica las referencias de un documento.
        max_referencias: limita cuántas verificar (útil en desarrollo)
        """
        referencias_a_verificar = referencias[:max_referencias]
        total = len(referencias_a_verificar)
        omitidas = len(referencias) - total

        encontradas = 0
        con_abstract = 0
        con_texto_completo = 0
        no_encontradas = 0

        logger.info(
            "iniciando_verificacion",
            total=total,
            omitidas=omitidas,
            doc_id=documento_id
        )

        for i, referencia in enumerate(referencias_a_verificar):
            logger.info(
                "verificando_referencia",
                numero=i + 1,
                total=total,
                titulo=referencia.titulo[:50],
            )

            resultado = self._verificar_una_referencia(referencia)

            if not resultado["encontrada"]:
                no_encontradas += 1
                self._marcar_no_encontrada(referencia.referencia_id)
                continue

            encontradas += 1
            if resultado["nivel_confianza"] == "texto_completo":
                con_texto_completo += 1
            elif resultado["nivel_confianza"] == "abstract":
                con_abstract += 1

            self._actualizar_neo4j(referencia.referencia_id, resultado)

        sin_texto = encontradas - con_abstract - con_texto_completo
        resumen = {
            "total": total,
            "encontradas": encontradas,
            "no_encontradas": no_encontradas,
            "con_texto_completo": con_texto_completo,
            "con_abstract_solo": con_abstract,
            "encontradas_sin_texto": sin_texto,
            "cobertura_porcentaje": round((encontradas / max(total, 1)) * 100, 1),
        }

        logger.info("verificacion_completada", **resumen)
        logger.info(
            "cobertura_texto_para_auditoria",
            texto_completo_pct=round((con_texto_completo / max(total, 1)) * 100, 1),
            solo_abstract_pct=round((con_abstract / max(total, 1)) * 100, 1),
            sin_texto_pct=round(((no_encontradas + sin_texto) / max(total, 1)) * 100, 1),
            advertencia=(
                "Alto porcentaje sin texto completo: la auditoría producirá muchos NO_INFO"
                if (no_encontradas + sin_texto) / max(total, 1) > 0.5 else None
            ),
        )
        return resumen

    def _verificar_una_referencia(self, referencia: ReferenciaAPA) -> dict:
        """
        Verifica una referencia usando SOLO su DOI. Sin DOI no se adivina:
        la búsqueda por título/autor generaba asociaciones erróneas.
        """
        doi = normalizar_doi(referencia.doi)
        if not doi:
            logger.info(
                "referencia_sin_doi_no_verificable",
                ref_id=referencia.referencia_id,
                titulo=referencia.titulo[:50],
            )
            return {
                "encontrada": False,
                "doi": None,
                "nivel_confianza": None,
                "texto": None,
            }

        # Metadatos oficiales por DOI directo (sin búsqueda difusa). Si
        # CrossRef falla igual seguimos: el DOI ya es confiable.
        resultado_crossref = crossref_service.obtener_por_doi(doi)
        titulo_oficial = resultado_crossref.titulo_oficial if resultado_crossref.encontrado else None
        texto_disponible = resultado_crossref.abstract if resultado_crossref.encontrado else None
        nivel_confianza = "abstract" if texto_disponible else None

        # Solo se salta la descarga si el índice usa el chunking actual;
        # papers con chunking viejo (páginas enteras) se re-indexan.
        if embedding_service.indexado_y_actualizado(doi):
            logger.info("paper_ya_en_cache", doi=doi)
            return {
                "encontrada": True,
                "doi": doi,
                "nivel_confianza": nivel_confianza or "cache",
                "texto": texto_disponible,
                "titulo_oficial": titulo_oficial,
                "score_coincidencia": 1.0,
            }

        resultado_unpaywall = unpaywall_service.buscar_pdf_gratuito(doi)
        if resultado_unpaywall.tiene_pdf_gratuito and resultado_unpaywall.texto_completo:
            texto_disponible = resultado_unpaywall.texto_completo
            nivel_confianza = "texto_completo"
            logger.info("usando_texto_completo", doi=doi)

        if texto_disponible:
            metadata = {
                "doi": doi,
                "doi_normalizado": doi_para_chunks(doi),
                "titulo": titulo_oficial or referencia.titulo,
                "anio": str(resultado_crossref.anio_oficial or referencia.anio or ""),
                "nivel_confianza": nivel_confianza,
                "referencia_id": referencia.referencia_id,
            }
            embedding_service.indexar_paper(
                doi=doi,
                texto=texto_disponible,
                metadata=metadata,
            )

        return {
            "encontrada": True,
            "doi": doi,
            # DOI válido pero sin texto descargable: se distingue de
            # 'no_encontrado' para que la UI explique el caso.
            "nivel_confianza": nivel_confianza or "sin_texto",
            "texto": texto_disponible,
            "titulo_oficial": titulo_oficial,
            "score_coincidencia": 1.0,
        }

    def _marcar_no_encontrada(self, referencia_id: str) -> None:
        query = """
        MATCH (r:Referencia {id: $ref_id})
        SET r.nivel_confianza = 'no_encontrado',
            r.verificado = true,
            r.verificado_en = datetime()
        """
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                session.run(query, ref_id=referencia_id)
        except Exception as e:
            logger.error("error_marcando_no_encontrada", ref_id=referencia_id, error=str(e))

    def _actualizar_neo4j(self, referencia_id: str, resultado: dict) -> None:
        """
        Actualiza el nodo Referencia en Neo4j con los resultados
        de la verificación externa.
        """
        query = """
        MATCH (r:Referencia {id: $ref_id})
        SET r.doi_verificado = $doi,
            r.titulo_oficial = $titulo_oficial,
            r.nivel_confianza = $nivel_confianza,
            r.score_crossref = $score,
            r.verificado = true,
            r.verificado_en = datetime()
        """
        try:
            with neo4j_service.driver.session(database=settings.neo4j_database) as session:
                session.run(
                    query,
                    ref_id=referencia_id,
                    doi=resultado.get("doi", ""),
                    titulo_oficial=resultado.get("titulo_oficial", ""),
                    nivel_confianza=resultado.get("nivel_confianza") or "no_encontrado",
                    score=resultado.get("score_coincidencia", 0.0),
                )
        except Exception as e:
            logger.error("error_actualizando_neo4j", ref_id=referencia_id, error=str(e))


# Singleton
verificacion_service = VerificacionService()
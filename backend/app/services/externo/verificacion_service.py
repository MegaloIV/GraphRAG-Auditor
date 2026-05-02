"""
Servicio orquestador de verificación externa de referencias.
Une CrossRef + Unpaywall + ChromaDB + Neo4j.

Flujo por referencia:
1. CrossRef  → ¿existe? + abstract
2. Unpaywall → ¿hay PDF gratuito? → texto completo
3. ChromaDB  → generar y guardar embedding
4. Neo4j     → actualizar nodo Referencia con resultados
"""
import structlog
from typing import Optional

from app.services.externo.crossref_service import crossref_service, ResultadoCrossRef
from app.services.externo.unpaywall_service import unpaywall_service
from app.services.externo.embedding_service import embedding_service
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
    ) -> dict:
        """
        Verifica todas las referencias de un documento.
        Retorna un resumen del proceso.
        """
        total = len(referencias)
        encontradas = 0
        con_abstract = 0
        con_texto_completo = 0
        no_encontradas = 0

        logger.info("iniciando_verificacion", total=total, doc_id=documento_id)

        for i, referencia in enumerate(referencias):
            logger.info(
                "verificando_referencia",
                numero=i + 1,
                total=total,
                titulo=referencia.titulo[:50],
            )

            resultado = self._verificar_una_referencia(referencia)

            if not resultado["encontrada"]:
                no_encontradas += 1
                continue

            encontradas += 1
            if resultado["nivel_confianza"] == "texto_completo":
                con_texto_completo += 1
            elif resultado["nivel_confianza"] == "abstract":
                con_abstract += 1

            # Actualizar nodo en Neo4j con los resultados
            self._actualizar_neo4j(referencia.referencia_id, resultado)

        resumen = {
            "total": total,
            "encontradas": encontradas,
            "no_encontradas": no_encontradas,
            "con_abstract": con_abstract,
            "con_texto_completo": con_texto_completo,
            "cobertura_porcentaje": round((encontradas / max(total, 1)) * 100, 1),
        }

        logger.info("verificacion_completada", **resumen)
        return resumen

    def _verificar_una_referencia(self, referencia: ReferenciaAPA) -> dict:
        """
        Verifica una sola referencia pasando por todas las capas.
        Retorna un dict con los resultados.
        """
        # ── CAPA 1: CrossRef ─────────────────────────────────
        resultado_crossref = crossref_service.buscar_referencia(
            titulo=referencia.titulo,
            autores=referencia.autores,
            anio=referencia.anio,
        )

        if not resultado_crossref.encontrado:
            return {
                "encontrada": False,
                "doi": None,
                "nivel_confianza": None,
                "texto": None,
            }

        doi = resultado_crossref.doi
        texto_disponible = resultado_crossref.abstract
        nivel_confianza = "abstract" if texto_disponible else None

        # ── CAPA 2: Unpaywall (si hay DOI) ───────────────────
        if doi:
            # Verificar cache primero
            if embedding_service.paper_ya_indexado(doi):
                logger.info("paper_ya_en_cache", doi=doi)
                return {
                    "encontrada": True,
                    "doi": doi,
                    "nivel_confianza": nivel_confianza or "cache",
                    "texto": texto_disponible,
                }

            resultado_unpaywall = unpaywall_service.buscar_pdf_gratuito(doi)

            if resultado_unpaywall.tiene_pdf_gratuito and resultado_unpaywall.texto_completo:
                texto_disponible = resultado_unpaywall.texto_completo
                nivel_confianza = "texto_completo"
                logger.info("usando_texto_completo", doi=doi)

        # ── CAPA 3: Generar embedding ─────────────────────────
        if texto_disponible and doi:
            metadata = {
                "doi": doi,
                "doi_normalizado": doi.replace("/", "_").replace(".", "_"),
                "titulo": resultado_crossref.titulo_oficial or referencia.titulo,
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
            "nivel_confianza": nivel_confianza,
            "texto": texto_disponible,
            "titulo_oficial": resultado_crossref.titulo_oficial,
            "score_coincidencia": resultado_crossref.score_coincidencia,
        }

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
            with neo4j_service.driver.session() as session:
                session.run(
                    query,
                    ref_id=referencia_id,
                    doi=resultado.get("doi", ""),
                    titulo_oficial=resultado.get("titulo_oficial", ""),
                    nivel_confianza=resultado.get("nivel_confianza", "no_encontrado"),
                    score=resultado.get("score_coincidencia", 0.0),
                )
        except Exception as e:
            logger.error("error_actualizando_neo4j", ref_id=referencia_id, error=str(e))


# Singleton
verificacion_service = VerificacionService()
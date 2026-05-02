import structlog
from unidecode import unidecode

from app.services.grafo.neo4j_service import neo4j_service
from app.models.grafo import ReferenciaAPA, CitaEnTexto, ResumenGrafo

logger = structlog.get_logger(__name__)


def _normalizar_nombre(nombre: str) -> str:
    """
    Convierte 'García, J.' en 'garcia, j.'
    Así dos referencias que citan al mismo autor
    con distinta tilde o mayúscula apuntan al mismo nodo.
    """
    return unidecode(nombre.strip().lower())


class GrafoCargaService:

    def cargar_documento(self, documento_id: str, nombre_archivo: str) -> None:
        """Crea o actualiza el nodo Documento en el grafo."""
        query = """
        MERGE (d:Documento {id: $id})
        SET d.nombre_archivo = $nombre,
            d.actualizado_en = datetime()
        """
        with neo4j_service.driver.session() as session:
            session.run(query, id=documento_id, nombre=nombre_archivo)
        logger.info("documento_cargado", doc_id=documento_id)

    def cargar_referencias(
        self,
        documento_id: str,
        referencias: list[ReferenciaAPA],
    ) -> int:
        """
        Carga referencias y sus autores al grafo.
        Crea relaciones TIENE_REFERENCIA y ESCRITO_POR.
        Retorna el número de referencias cargadas.
        """
        cargadas = 0
        with neo4j_service.driver.session() as session:
            for ref in referencias:

                # MERGE de la referencia
                query_ref = """
                MERGE (r:Referencia {id: $ref_id})
                SET r.titulo = $titulo,
                    r.anio = $anio,
                    r.fuente = $fuente,
                    r.doi = $doi,
                    r.datos_incompletos = $incompleto
                WITH r
                MATCH (d:Documento {id: $doc_id})
                MERGE (d)-[:TIENE_REFERENCIA]->(r)
                """
                session.run(
                    query_ref,
                    ref_id=ref.referencia_id,
                    titulo=ref.titulo,
                    anio=ref.anio,
                    fuente=ref.fuente or "",
                    doi=ref.doi or "",
                    incompleto=ref.datos_incompletos,
                    doc_id=documento_id,
                )

                # MERGE de cada autor y relación ESCRITO_POR
                for nombre in ref.autores:
                    nombre_norm = _normalizar_nombre(nombre)
                    query_autor = """
                    MERGE (a:Autor {nombre_normalizado: $nombre_norm})
                    SET a.nombre = $nombre
                    WITH a
                    MATCH (r:Referencia {id: $ref_id})
                    MERGE (r)-[:ESCRITO_POR]->(a)
                    """
                    session.run(
                        query_autor,
                        nombre_norm=nombre_norm,
                        nombre=nombre,
                        ref_id=ref.referencia_id,
                    )

                cargadas += 1

        logger.info("referencias_cargadas", total=cargadas, doc_id=documento_id)
        return cargadas

    def cargar_citas(
        self,
        documento_id: str,
        citas: list[CitaEnTexto],
    ) -> int:
        """
        Carga citas al grafo y las vincula al documento.
        Crea relación TIENE_CITA.
        Retorna el número de citas cargadas.
        """
        cargadas = 0
        with neo4j_service.driver.session() as session:
            for cita in citas:
                query_cita = """
                MERGE (c:Cita {id: $cita_id})
                SET c.texto = $texto,
                    c.tipo = $tipo,
                    c.pagina = $pagina,
                    c.fragmento = $fragmento
                WITH c
                MATCH (d:Documento {id: $doc_id})
                MERGE (d)-[:TIENE_CITA]->(c)
                """
                session.run(
                    query_cita,
                    cita_id=cita.cita_id,
                    texto=cita.texto_cita,
                    tipo=cita.tipo.value,
                    pagina=cita.pagina,
                    fragmento=cita.fragmento_oracion,
                    doc_id=documento_id,
                )
                cargadas += 1

        logger.info("citas_cargadas", total=cargadas, doc_id=documento_id)
        return cargadas

    def obtener_resumen_grafo(self, documento_id: str) -> ResumenGrafo:
        """
        Genera el resumen del grafo para HU-006.
        Calcula densidad y determina si es robusto (> 3 rel/nodo).
        """
        conteo = neo4j_service.contar_nodos_y_relaciones(documento_id)

        total_nodos = conteo["referencias"] + conteo["citas"] + conteo["autores"]
        total_relaciones = conteo["relaciones"]
        densidad = round(total_relaciones / max(total_nodos, 1), 2)
        grafo_robusto = densidad >= 3.0

        advertencia = None
        if not grafo_robusto:
            advertencia = (
                f"La densidad del grafo es {densidad} relaciones/nodo, "
                f"por debajo del mínimo recomendado de 3. "
                f"El documento puede tener pocas referencias o citas."
            )

        return ResumenGrafo(
            documento_id=documento_id,
            total_nodos=total_nodos,
            nodos_autores=conteo["autores"],
            nodos_referencias=conteo["referencias"],
            nodos_citas=conteo["citas"],
            total_relaciones=total_relaciones,
            densidad_promedio=densidad,
            grafo_robusto=grafo_robusto,
            advertencia_densidad=advertencia,
        )


# Singleton
grafo_carga_service = GrafoCargaService()
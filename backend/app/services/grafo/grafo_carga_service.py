import re
import structlog
from unidecode import unidecode

from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service
from app.models.grafo import ReferenciaAPA, CitaEnTexto, ResumenGrafo

logger = structlog.get_logger(__name__)
settings = get_settings()


def _normalizar_nombre(nombre: str) -> str:
    """
    Convierte 'García, J.' en 'garcia, j.'
    Así dos referencias que citan al mismo autor
    con distinta tilde o mayúscula apuntan al mismo nodo.
    """
    return unidecode(nombre.strip().lower())


def _normalizar_apellido(texto: str) -> str:
    """Minúsculas sin tildes para comparar apellidos entre cita y referencia."""
    return unidecode(texto.strip().lower())


class GrafoCargaService:

    def cargar_documento(self, documento_id: str, nombre_archivo: str) -> None:
        """Crea o actualiza el nodo Documento en el grafo."""
        query = """
        MERGE (d:Documento {id: $id})
        SET d.nombre_archivo = $nombre,
            d.actualizado_en = datetime()
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
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
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
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
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
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

    def vincular_citas(self, documento_id: str) -> dict:
        """
        Vincula cada Cita del documento con su Referencia en Neo4j
        creando la relación (Cita)-[:CITA_A]->(Referencia).

        Matching por apellido + año extraídos del texto de la cita.
        Estrategias en cascada:
          1. apellido exacto + año  → confianza 0.95
          2. apellido parcial + año → confianza 0.75
          3. solo año (única ref ese año) → confianza 0.50

        Retorna {"vinculadas": int, "no_vinculadas": int, "total": int}
        """
        # Leer citas sin vincular y referencias del documento
        q_citas = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
        WHERE NOT (c)-[:CITA_A]->()
        RETURN c.id AS cita_id, c.texto AS texto_cita
        """
        q_refs = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
        OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
        RETURN r.id AS ref_id, r.anio AS anio, collect(a.nombre) AS autores
        """
        q_crear = """
        MATCH (c:Cita {id: $cita_id})
        MATCH (r:Referencia {id: $ref_id})
        MERGE (c)-[rel:CITA_A]->(r)
        SET rel.confianza = $confianza, rel.metodo = $metodo
        """

        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            citas_raw = list(session.run(q_citas, doc_id=documento_id))
            refs_raw  = list(session.run(q_refs,  doc_id=documento_id))

        if not citas_raw:
            logger.info("vinculacion_sin_citas_pendientes", doc_id=documento_id)
            return {"vinculadas": 0, "no_vinculadas": 0, "total": 0}

        if not refs_raw:
            logger.warning("vinculacion_sin_referencias", doc_id=documento_id)
            return {"vinculadas": 0, "no_vinculadas": len(citas_raw), "total": len(citas_raw)}

        # Construir índice: {(apellido_norm, anio): ref_id}
        indice: dict[tuple, str] = {}
        for rec in refs_raw:
            for autor in (rec["autores"] or []):
                apellido = _normalizar_apellido(autor.split(",")[0])
                if apellido:
                    indice.setdefault((apellido, rec["anio"]), rec["ref_id"])

        excluir = {"et", "al", "and", "y", "en", "de", "del", "la", "el"}
        vinculadas = 0
        no_vinculadas = 0

        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            for registro in citas_raw:
                texto  = registro["texto_cita"] or ""
                anio_m = re.search(r'\b(19|20)\d{2}\b', texto)
                anio   = int(anio_m.group()) if anio_m else None

                apellidos = [
                    _normalizar_apellido(a)
                    for a in re.findall(r'[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ\-]+', texto)
                    if a.lower() not in excluir and len(a) > 2
                ]

                ref_id = confianza = metodo = None

                if apellidos and anio:
                    # Estrategia 1: exacto
                    for ap in apellidos:
                        if (ap, anio) in indice:
                            ref_id, confianza, metodo = indice[(ap, anio)], 0.95, "apellido_anio_exacto"
                            break

                    # Estrategia 2: parcial
                    if not ref_id:
                        for ap in apellidos:
                            for (ap_idx, anio_idx), rid in indice.items():
                                if anio_idx == anio and (ap_idx.startswith(ap) or ap.startswith(ap_idx)):
                                    ref_id, confianza, metodo = rid, 0.75, "apellido_parcial_anio"
                                    break
                            if ref_id:
                                break

                    # Estrategia 3: solo año
                    if not ref_id and len(apellidos) == 1:
                        candidatos = [rid for (_, anio_idx), rid in indice.items() if anio_idx == anio]
                        if len(candidatos) == 1:
                            ref_id, confianza, metodo = candidatos[0], 0.50, "solo_anio"

                if ref_id:
                    session.run(
                        q_crear,
                        cita_id=registro["cita_id"],
                        ref_id=ref_id,
                        confianza=confianza,
                        metodo=metodo,
                    )
                    vinculadas += 1
                    logger.debug("cita_vinculada", cita=texto, ref_id=ref_id, confianza=confianza)
                else:
                    no_vinculadas += 1
                    logger.debug("cita_no_vinculada", cita=texto)

        total = vinculadas + no_vinculadas
        logger.info(
            "vinculacion_completada",
            doc_id=documento_id,
            vinculadas=vinculadas,
            no_vinculadas=no_vinculadas,
            tasa=f"{vinculadas/total:.0%}" if total else "0%",
        )
        return {"vinculadas": vinculadas, "no_vinculadas": no_vinculadas, "total": total}

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
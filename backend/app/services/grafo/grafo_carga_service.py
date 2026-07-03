import re
import structlog
from unidecode import unidecode

from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service
from app.models.grafo import ReferenciaAPA, CitaEnTexto, ResumenGrafo, TipoCita

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
        (La antigua estrategia 3 —solo año— fue eliminada por generar falsos positivos.)

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
                    # Estrategia 1: apellido exacto + año
                    for ap in apellidos:
                        if (ap, anio) in indice:
                            ref_id, confianza, metodo = indice[(ap, anio)], 0.95, "apellido_anio_exacto"
                            break

                    # Estrategia 2: apellido parcial + año
                    # (cubre truncaciones como "Rodríguez" vs "Rodriguez")
                    if not ref_id:
                        for ap in apellidos:
                            for (ap_idx, anio_idx), rid in indice.items():
                                if anio_idx == anio and (ap_idx.startswith(ap) or ap.startswith(ap_idx)):
                                    ref_id, confianza, metodo = rid, 0.75, "apellido_parcial_anio"
                                    break
                            if ref_id:
                                break

                    # Estrategia 3 eliminada: enlazar solo por año sin coincidencia de
                    # apellido generaba falsos positivos (ej. "Zhao et al." → "Wang et al."
                    # cuando ambos comparten el mismo año y es la única referencia de ese año).

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

    # ── CRUD de revisión humana ──────────────────────────────────────────────

    def _leer_cita(self, session, cita_id: str) -> CitaEnTexto | None:
        q = """
        MATCH (c:Cita {id: $cita_id})
        OPTIONAL MATCH (c)-[:CITA_A]->(r:Referencia)
        RETURN c, r.id AS referencia_id
        """
        rec = session.run(q, cita_id=cita_id).single()
        if not rec:
            return None
        c = rec["c"]
        tipo = TipoCita.PARENTETICA if c.get("tipo") == "parentetica" else TipoCita.NARRATIVA
        return CitaEnTexto(
            cita_id=c["id"],
            texto_cita=c.get("texto", ""),
            tipo=tipo,
            pagina=c.get("pagina", 0),
            fragmento_oracion=c.get("fragmento", ""),
            referencia_id=rec["referencia_id"],
        )

    def actualizar_cita(self, cita_id: str, campos: dict) -> CitaEnTexto | None:
        """Actualiza solo los campos presentes. `campos` usa nombres del request."""
        mapa = {
            "texto_cita": "texto",
            "tipo": "tipo",
            "pagina": "pagina",
            "fragmento_oracion": "fragmento",
        }
        # referencia_id se gestiona aparte (relación CITA_A, no propiedad).
        revincular = "referencia_id" in campos
        referencia_id = campos.get("referencia_id")
        sets = {mapa[k]: v for k, v in campos.items() if k in mapa}
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            if sets:
                set_clause = ", ".join(f"c.{prop} = ${prop}" for prop in sets)
                session.run(
                    f"MATCH (c:Cita {{id: $cita_id}}) SET {set_clause}",
                    cita_id=cita_id,
                    **sets,
                )
            if revincular:
                # Eliminar el vínculo previo y, si se indicó una referencia, recrearlo.
                session.run(
                    "MATCH (c:Cita {id: $cita_id})-[rel:CITA_A]->() DELETE rel",
                    cita_id=cita_id,
                )
                if referencia_id:
                    session.run(
                        """
                        MATCH (c:Cita {id: $cita_id})
                        MATCH (r:Referencia {id: $ref_id})
                        MERGE (c)-[rel:CITA_A]->(r)
                        SET rel.confianza = 1.0, rel.metodo = 'manual'
                        """,
                        cita_id=cita_id,
                        ref_id=referencia_id,
                    )
            return self._leer_cita(session, cita_id)

    def crear_cita(self, documento_id: str, cita: CitaEnTexto) -> CitaEnTexto:
        """Crea un nodo Cita y lo vincula al documento con TIENE_CITA."""
        query = """
        MATCH (d:Documento {id: $doc_id})
        CREATE (c:Cita {
            id: $cita_id,
            texto: $texto,
            tipo: $tipo,
            pagina: $pagina,
            fragmento: $fragmento
        })
        MERGE (d)-[:TIENE_CITA]->(c)
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            session.run(
                query,
                doc_id=documento_id,
                cita_id=cita.cita_id,
                texto=cita.texto_cita,
                tipo=cita.tipo.value,
                pagina=cita.pagina,
                fragmento=cita.fragmento_oracion,
            )
            return self._leer_cita(session, cita.cita_id)

    def eliminar_cita(self, cita_id: str) -> None:
        """Elimina la Cita y todas sus relaciones (TIENE_CITA, CITA_A)."""
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            session.run("MATCH (c:Cita {id: $cita_id}) DETACH DELETE c", cita_id=cita_id)
        logger.info("cita_eliminada", cita_id=cita_id)

    def _leer_referencia(self, session, referencia_id: str) -> ReferenciaAPA | None:
        q = """
        MATCH (r:Referencia {id: $ref_id})
        OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
        RETURN r, collect(a.nombre) AS autores
        """
        rec = session.run(q, ref_id=referencia_id).single()
        if not rec:
            return None
        r = rec["r"]
        return ReferenciaAPA(
            referencia_id=r["id"],
            autores=[a for a in rec["autores"] if a],
            anio=r.get("anio"),
            titulo=r.get("titulo", ""),
            fuente=r.get("fuente"),
            doi=r.get("doi_verificado") or r.get("doi"),
            datos_incompletos=r.get("datos_incompletos", False),
            campos_faltantes=[],
            nivel_confianza=r.get("nivel_confianza"),
        )

    def _set_autores(self, session, referencia_id: str, autores: list[str]) -> None:
        """Reemplaza los Autor de una referencia: borra rels previas y recrea."""
        session.run(
            "MATCH (r:Referencia {id: $ref_id})-[rel:ESCRITO_POR]->(:Autor) DELETE rel",
            ref_id=referencia_id,
        )
        for nombre in autores:
            session.run(
                """
                MERGE (a:Autor {nombre_normalizado: $nombre_norm})
                SET a.nombre = $nombre
                WITH a
                MATCH (r:Referencia {id: $ref_id})
                MERGE (r)-[:ESCRITO_POR]->(a)
                """,
                nombre_norm=_normalizar_nombre(nombre),
                nombre=nombre,
                ref_id=referencia_id,
            )

    def actualizar_referencia(self, referencia_id: str, campos: dict) -> ReferenciaAPA | None:
        """Actualiza campos escalares; si `autores` está presente, recrea los nodos Autor."""
        autores = campos.pop("autores", None)
        escalares = {k: v for k, v in campos.items()
                     if k in ("anio", "titulo", "fuente", "doi", "datos_incompletos", "campos_faltantes")}
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            if escalares:
                set_clause = ", ".join(f"r.{prop} = ${prop}" for prop in escalares)
                session.run(
                    f"MATCH (r:Referencia {{id: $ref_id}}) SET {set_clause}",
                    ref_id=referencia_id,
                    **escalares,
                )
            if autores is not None:
                self._set_autores(session, referencia_id, autores)
            return self._leer_referencia(session, referencia_id)

    def crear_referencia(self, documento_id: str, ref: ReferenciaAPA) -> ReferenciaAPA:
        """Crea un nodo Referencia con sus Autores y lo vincula al documento."""
        query = """
        MATCH (d:Documento {id: $doc_id})
        CREATE (r:Referencia {
            id: $ref_id,
            titulo: $titulo,
            anio: $anio,
            fuente: $fuente,
            doi: $doi,
            datos_incompletos: $incompleto
        })
        MERGE (d)-[:TIENE_REFERENCIA]->(r)
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            session.run(
                query,
                doc_id=documento_id,
                ref_id=ref.referencia_id,
                titulo=ref.titulo,
                anio=ref.anio,
                fuente=ref.fuente or "",
                doi=ref.doi or "",
                incompleto=ref.datos_incompletos,
            )
            self._set_autores(session, ref.referencia_id, ref.autores)
            return self._leer_referencia(session, ref.referencia_id)

    def eliminar_referencia(self, referencia_id: str) -> None:
        """
        Elimina la Referencia (y sus rels TIENE_REFERENCIA / CITA_A) y luego los
        nodos Autor que queden huérfanos (sin otras referencias).
        """
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            autores = [
                rec["nn"]
                for rec in session.run(
                    "MATCH (r:Referencia {id: $ref_id})-[:ESCRITO_POR]->(a:Autor) "
                    "RETURN a.nombre_normalizado AS nn",
                    ref_id=referencia_id,
                )
            ]
            session.run("MATCH (r:Referencia {id: $ref_id}) DETACH DELETE r", ref_id=referencia_id)
            for nn in autores:
                session.run(
                    "MATCH (a:Autor {nombre_normalizado: $nn}) "
                    "WHERE NOT (a)<-[:ESCRITO_POR]-() DETACH DELETE a",
                    nn=nn,
                )
        logger.info("referencia_eliminada", ref_id=referencia_id)

    def obtener_resumen_grafo(self, documento_id: str) -> ResumenGrafo:
        conteo = neo4j_service.contar_nodos_y_relaciones(documento_id)

        total_nodos      = conteo["referencias"] + conteo["citas"] + conteo["autores"]
        total_relaciones = conteo["relaciones"]
        densidad         = round(total_relaciones / max(total_nodos, 1), 2)
        grafo_robusto    = densidad >= 3.0

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
            citas_vinculadas=conteo.get("citas_vinculadas", 0),
            advertencia_densidad=advertencia,
        )


# Singleton
grafo_carga_service = GrafoCargaService()
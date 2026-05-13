import structlog
from neo4j import GraphDatabase, Driver
from neo4j.exceptions import ServiceUnavailable, AuthError

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

CONSTRAINTS = [
    "CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Documento) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT ref_id IF NOT EXISTS FOR (r:Referencia) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT cita_id IF NOT EXISTS FOR (c:Cita) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT autor_nombre IF NOT EXISTS FOR (a:Autor) REQUIRE a.nombre_normalizado IS UNIQUE",
]

INDICES = [
    "CREATE INDEX autor_nombre_idx IF NOT EXISTS FOR (a:Autor) ON (a.nombre)",
    "CREATE INDEX ref_titulo_idx IF NOT EXISTS FOR (r:Referencia) ON (r.titulo)",
    "CREATE INDEX ref_anio_idx IF NOT EXISTS FOR (r:Referencia) ON (r.anio)",
    "CREATE INDEX cita_texto_idx IF NOT EXISTS FOR (c:Cita) ON (c.texto)",
]


class Neo4jService:

    def __init__(self):
        self._driver: Driver | None = None

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            self._driver = self._conectar()
        else:
            try:
                self._driver.verify_connectivity()
            except Exception:
                logger.warning("neo4j_reconectando")
                self._driver = self._conectar()
        return self._driver

    def _conectar(self) -> Driver:
        if not all([settings.neo4j_uri, settings.neo4j_password]):
            raise RuntimeError(
                "Credenciales de Neo4j no configuradas. "
                "Verifica NEO4J_URI y NEO4J_PASSWORD en tu .env"
            )
        try:
            driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password),
            )
            driver.verify_connectivity()
            logger.info("neo4j_conectado", uri=settings.neo4j_uri)
            return driver
        except AuthError:
            raise RuntimeError(
                "Credenciales de Neo4j incorrectas. "
                "Verifica usuario y contraseña en .env"
            )
        except ServiceUnavailable:
            raise RuntimeError(
                "No se pudo conectar a Neo4j. "
                "Verifica que la instancia AuraDB esté activa."
            )

    def inicializar_schema(self) -> None:
        with self.driver.session(database=settings.neo4j_database) as session:
            for query in CONSTRAINTS:
                session.run(query)
            for query in INDICES:
                session.run(query)
        logger.info("neo4j_schema_inicializado")

    def contar_nodos_y_relaciones(self, documento_id: str) -> dict:
        """
        Cuenta nodos y relaciones reales del grafo para un documento.
        Incluye CITA_A para densidad correcta.
        """
        query = """
        MATCH (d:Documento {id: $doc_id})
        OPTIONAL MATCH (d)-[:TIENE_REFERENCIA]->(r:Referencia)
        OPTIONAL MATCH (d)-[:TIENE_CITA]->(c:Cita)
        OPTIONAL MATCH (r)-[:ESCRITO_POR]->(a:Autor)
        RETURN
            count(DISTINCT r) AS referencias,
            count(DISTINCT c) AS citas,
            count(DISTINCT a) AS autores
        """
        query_cita_a = """
        MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)-[:CITA_A]->()
        RETURN count(c) AS citas_vinculadas
        """
        with self.driver.session(database=settings.neo4j_database) as session:
            result       = session.run(query, doc_id=documento_id).single()
            cita_a_res   = session.run(query_cita_a, doc_id=documento_id).single()

            if not result:
                return {"referencias": 0, "citas": 0, "autores": 0, "relaciones": 0, "citas_vinculadas": 0}

            referencias      = result["referencias"]
            citas            = result["citas"]
            autores          = result["autores"]
            citas_vinculadas = cita_a_res["citas_vinculadas"] if cita_a_res else 0

            # Relaciones reales: TIENE_REFERENCIA + TIENE_CITA + ESCRITO_POR + CITA_A
            relaciones = referencias + citas + autores + citas_vinculadas

            return {
                "referencias": referencias,
                "citas": citas,
                "autores": autores,
                "relaciones": relaciones,
                "citas_vinculadas": citas_vinculadas,
            }

    def cerrar(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("neo4j_conexion_cerrada")


# Singleton
neo4j_service = Neo4jService()
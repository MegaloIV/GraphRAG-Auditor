"""
Persistencia del estado del pipeline en Supabase Postgres.

Reemplaza los ficheros JSON de ./data/progreso. El SSE consulta este estado en
bucle, por lo que se usa una tabla en Postgres (lectura rápida y barata) en lugar
del bucket de objetos.

Tabla: auditoria_progreso (documento_id PK, datos JSONB, actualizado_at)
"""
import json

import psycopg2
import psycopg2.extras
import structlog

from app.core.config import get_settings
from app.models.ingesta import ProgresoAuditoriaResponse

logger = structlog.get_logger(__name__)


class ProgresoRepository:

    def __init__(self):
        self._conn = None

    def _conectar(self):
        if self._conn is None or self._conn.closed:
            s = get_settings()
            self._conn = psycopg2.connect(
                host=s.supabase_db_host,
                port=s.supabase_db_port,
                dbname=s.supabase_db_name,
                user=s.supabase_db_user,
                password=s.supabase_db_password,
                connect_timeout=10,
            )
        return self._conn

    def inicializar_schema(self) -> None:
        """Crea la tabla si no existe. Idempotente."""
        conn = self._conectar()
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS auditoria_progreso (
                    documento_id   TEXT PRIMARY KEY,
                    datos          JSONB NOT NULL,
                    actualizado_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()
        logger.info("progreso_schema_inicializado")

    def guardar(self, progreso: ProgresoAuditoriaResponse) -> None:
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auditoria_progreso (documento_id, datos, actualizado_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (documento_id)
                    DO UPDATE SET datos = EXCLUDED.datos, actualizado_at = now()
                    """,
                    (progreso.documento_id, progreso.model_dump_json()),
                )
            conn.commit()
        except Exception as e:
            logger.error("progreso_guardar_error", doc_id=progreso.documento_id, error=str(e))
            if self._conn is not None and not self._conn.closed:
                self._conn.rollback()

    def leer(self, documento_id: str) -> ProgresoAuditoriaResponse | None:
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT datos FROM auditoria_progreso WHERE documento_id = %s",
                    (documento_id,),
                )
                fila = cur.fetchone()
            if not fila:
                return None
            datos = fila[0]
            if isinstance(datos, str):
                datos = json.loads(datos)
            return ProgresoAuditoriaResponse(**datos)
        except Exception as e:
            logger.error("progreso_leer_error", doc_id=documento_id, error=str(e))
            if self._conn is not None and not self._conn.closed:
                self._conn.rollback()
            return None


progreso_repository = ProgresoRepository()

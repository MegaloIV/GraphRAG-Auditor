"""
Servicio de almacenamiento vectorial con Supabase + pgvector.
Reemplaza ChromaDB: almacena y consulta embeddings de papers en PostgreSQL.

Tabla: papers_chunks
Índice ANN: ivfflat con distancia coseno
"""
import structlog
import numpy as np
import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector
from typing import Optional

from app.core.config import get_settings

logger = structlog.get_logger(__name__)


class SupabaseVectorService:

    def __init__(self):
        self._conn = None

    # ── Conexión ─────────────────────────────────────────────────────────────

    def _conectar(self):
        """Retorna la conexión activa; reconecta si está cerrada o rota."""
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
            register_vector(self._conn)
            logger.info("supabase_conectado", host=s.supabase_db_host)
        return self._conn

    # ── Lectura ───────────────────────────────────────────────────────────────

    def paper_ya_indexado(self, doi_normalizado: str) -> bool:
        """Devuelve True si ya existen chunks para este DOI."""
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM papers_chunks WHERE doi_normalizado = %s",
                    (doi_normalizado,),
                )
                return cur.fetchone()[0] > 0
        except Exception as e:
            logger.error("error_paper_ya_indexado", doi_normalizado=doi_normalizado, error=str(e))
            return False

    def buscar_similares(
        self,
        embedding: list[float],
        doi_normalizado: Optional[str] = None,
        top_k: int = 3,
    ) -> list[dict]:
        """
        Devuelve los chunks más similares al embedding dado.

        Args:
            embedding:       Vector de consulta (lista de floats, dim 1536).
            doi_normalizado: Si se indica, restringe la búsqueda a ese paper.
                             Si es None, busca en todos los papers.
            top_k:           Máximo de resultados a devolver.

        Returns:
            Lista de dicts con: id, contenido, pagina, titulo, doi,
            chunk_index, similitud (float 0-1).
        """
        try:
            conn = self._conectar()
            vec = np.array(embedding, dtype=np.float32)

            if doi_normalizado:
                sql = """
                    SELECT id, contenido, pagina, titulo, doi, chunk_index,
                           1 - (embedding <=> %s) AS similitud
                    FROM papers_chunks
                    WHERE doi_normalizado = %s
                    ORDER BY embedding <=> %s
                    LIMIT %s
                """
                params = (vec, doi_normalizado, vec, top_k)
            else:
                sql = """
                    SELECT id, contenido, pagina, titulo, doi, chunk_index,
                           1 - (embedding <=> %s) AS similitud
                    FROM papers_chunks
                    ORDER BY embedding <=> %s
                    LIMIT %s
                """
                params = (vec, vec, top_k)

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(f) for f in cur.fetchall()]

        except Exception as e:
            logger.error("error_buscar_similares", doi_normalizado=doi_normalizado, error=str(e))
            return []

    def chunks_por_rango(
        self,
        doi_normalizado: str,
        desde: int,
        hasta: int,
    ) -> list[dict]:
        """
        Chunks contiguos de un paper por rango de chunk_index (inclusive),
        ordenados. Se usa para coser la ventana de contexto alrededor del
        mejor chunk recuperado (los chunks no se solapan entre sí).
        """
        try:
            conn = self._conectar()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, contenido, pagina, titulo, doi, chunk_index
                    FROM papers_chunks
                    WHERE doi_normalizado = %s AND chunk_index BETWEEN %s AND %s
                    ORDER BY chunk_index
                    """,
                    (doi_normalizado, desde, hasta),
                )
                return [dict(f) for f in cur.fetchall()]
        except Exception as e:
            logger.error("error_chunks_por_rango", doi_normalizado=doi_normalizado, error=str(e))
            return []

    def total_chunks(self) -> int:
        """Retorna el total de chunks almacenados."""
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM papers_chunks")
                return cur.fetchone()[0]
        except Exception as e:
            logger.error("error_total_chunks", error=str(e))
            return 0

    # ── Escritura ─────────────────────────────────────────────────────────────

    def indexar_chunks(self, chunks: list[dict]) -> bool:
        """
        Inserta o actualiza chunks en papers_chunks (idempotente).

        Cada dict debe tener:
          id, doi, doi_normalizado, titulo, anio, pagina, chunk_index,
          nivel_confianza, referencia_id, contenido, embedding.
        """
        if not chunks:
            return False
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    """
                    INSERT INTO papers_chunks
                        (id, doi, doi_normalizado, titulo, anio, pagina,
                         chunk_index, nivel_confianza, referencia_id,
                         contenido, embedding)
                    VALUES
                        (%(id)s, %(doi)s, %(doi_normalizado)s, %(titulo)s,
                         %(anio)s, %(pagina)s, %(chunk_index)s,
                         %(nivel_confianza)s, %(referencia_id)s,
                         %(contenido)s, %(embedding)s)
                    ON CONFLICT (id) DO UPDATE SET
                        doi             = EXCLUDED.doi,
                        doi_normalizado = EXCLUDED.doi_normalizado,
                        titulo          = EXCLUDED.titulo,
                        anio            = EXCLUDED.anio,
                        pagina          = EXCLUDED.pagina,
                        chunk_index     = EXCLUDED.chunk_index,
                        nivel_confianza = EXCLUDED.nivel_confianza,
                        referencia_id   = EXCLUDED.referencia_id,
                        contenido       = EXCLUDED.contenido,
                        embedding       = EXCLUDED.embedding
                    """,
                    chunks,
                )
            conn.commit()
            logger.info("chunks_indexados", total=len(chunks))
            return True
        except Exception as e:
            if self._conn:
                self._conn.rollback()
            logger.error("error_indexar_chunks", error=str(e))
            return False

    def eliminar_chunks_por_doi(self, doi_normalizado: str) -> None:
        """Elimina todos los chunks de un DOI antes de re-indexar."""
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM papers_chunks WHERE doi_normalizado = %s",
                    (doi_normalizado,),
                )
            conn.commit()
            logger.info("chunks_eliminados", doi_normalizado=doi_normalizado)
        except Exception as e:
            if self._conn:
                self._conn.rollback()
            logger.error("error_eliminar_chunks", doi_normalizado=doi_normalizado, error=str(e))


# Singleton
supabase_vector_service = SupabaseVectorService()

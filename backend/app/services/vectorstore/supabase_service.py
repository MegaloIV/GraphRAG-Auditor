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
                    SELECT id, contenido, pagina, titulo, doi, chunk_index, seccion,
                           1 - (embedding <=> %s) AS similitud
                    FROM papers_chunks
                    WHERE doi_normalizado = %s
                    ORDER BY embedding <=> %s
                    LIMIT %s
                """
                params = (vec, doi_normalizado, vec, top_k)
            else:
                sql = """
                    SELECT id, contenido, pagina, titulo, doi, chunk_index, seccion,
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

    def chunks_son_actuales(self, doi_normalizado: str) -> bool:
        """
        True si los chunks del DOI están al día: usan el chunking de ~1100
        chars Y llevan la sección del paper. Los indexados antes de cualquiera
        de las dos cosas devuelven False para que se re-indexen.
        """
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(LENGTH(contenido)), 0), "
                    "       COUNT(*) FILTER (WHERE seccion IS NULL) "
                    "FROM papers_chunks WHERE doi_normalizado = %s",
                    (doi_normalizado,),
                )
                maximo, sin_seccion = cur.fetchone()
                return 0 < maximo <= 1600 and sin_seccion == 0
        except Exception as e:
            logger.error("error_chunks_son_actuales", doi_normalizado=doi_normalizado, error=str(e))
            return True  # ante la duda, no forzar re-descarga del paper

    def nivel_confianza_indexado(self, doi_normalizado: str) -> str | None:
        """
        Con qué calidad de texto se indexó el paper ('texto_completo',
        'abstract', ...). None si no está indexado. Sirve para no dar por
        bueno un abstract cuando todavía se puede conseguir el paper entero.
        """
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT nivel_confianza FROM papers_chunks "
                    "WHERE doi_normalizado = %s LIMIT 1",
                    (doi_normalizado,),
                )
                fila = cur.fetchone()
                return fila[0] if fila else None
        except Exception as e:
            logger.error("error_nivel_confianza", doi_normalizado=doi_normalizado, error=str(e))
            return None

    def buscar_por_termino(
        self,
        doi_normalizado: str,
        termino: str,
        limit: int = 2,
    ) -> list[dict]:
        """
        Chunks de un paper que contienen un término exacto (ancla léxica,
        p. ej. la cifra "6.7" de la afirmación del tesista). Complementa la
        búsqueda vectorial cuando la consulta y el paper están en idiomas
        distintos.
        """
        try:
            conn = self._conectar()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, contenido, pagina, titulo, doi, chunk_index, seccion
                    FROM papers_chunks
                    WHERE doi_normalizado = %s AND contenido ILIKE %s
                    ORDER BY chunk_index
                    LIMIT %s
                    """,
                    (doi_normalizado, f"%{termino}%", limit),
                )
                return [dict(f) for f in cur.fetchall()]
        except Exception as e:
            logger.error("error_buscar_por_termino", termino=termino, error=str(e))
            return []

    def total_papers_distintos(self) -> int:
        """Papers distintos indexados. El índice es por DOI y compartido entre
        documentos, así que este número es el corpus acumulado."""
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(DISTINCT doi_normalizado) FROM papers_chunks")
                return cur.fetchone()[0]
        except Exception as e:
            logger.error("error_total_papers", error=str(e))
            return 0

    def catalogo_texto_completo(self) -> list[dict]:
        """
        Papers de la biblioteca con texto completo: uno por clave, con su
        título y año. La tabla es compartida entre tesis, así que un paper
        conseguido para una tesis sirve para cualquier otra que lo cite.
        """
        try:
            conn = self._conectar()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (doi_normalizado)
                           doi_normalizado, doi, titulo, anio, nivel_confianza
                    FROM papers_chunks
                    WHERE nivel_confianza IN ('texto_completo', 'manual', 'pagina_web')
                    ORDER BY doi_normalizado
                    """
                )
                return [dict(f) for f in cur.fetchall()]
        except Exception as e:
            logger.error("error_catalogo_biblioteca", error=str(e))
            return []

    def texto_de_paper(self, doi_normalizado: str) -> str:
        """Texto del paper reconstruido de sus chunks (para copias de respaldo)."""
        try:
            conn = self._conectar()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT contenido FROM papers_chunks WHERE doi_normalizado = %s ORDER BY chunk_index",
                    (doi_normalizado,),
                )
                return "\n\n".join(f[0] for f in cur.fetchall() if f[0])
        except Exception as e:
            logger.error("error_texto_de_paper", doi_normalizado=doi_normalizado, error=str(e))
            return ""

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
                         contenido, seccion, embedding)
                    VALUES
                        (%(id)s, %(doi)s, %(doi_normalizado)s, %(titulo)s,
                         %(anio)s, %(pagina)s, %(chunk_index)s,
                         %(nivel_confianza)s, %(referencia_id)s,
                         %(contenido)s, %(seccion)s, %(embedding)s)
                    ON CONFLICT (id) DO UPDATE SET
                        doi             = EXCLUDED.doi,
                        doi_normalizado = EXCLUDED.doi_normalizado,
                        titulo          = EXCLUDED.titulo,
                        anio            = EXCLUDED.anio,
                        pagina          = EXCLUDED.pagina,
                        chunk_index     = EXCLUDED.chunk_index,
                        nivel_confianza = EXCLUDED.nivel_confianza,
                        seccion = EXCLUDED.seccion,
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

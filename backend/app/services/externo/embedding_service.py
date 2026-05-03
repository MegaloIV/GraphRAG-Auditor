"""
Servicio de embeddings con ChromaDB + sentence-transformers.
Genera embeddings del texto/abstract de cada paper y los
almacena en ChromaDB para búsqueda semántica en EP-003.

Modelo: all-MiniLM-L6-v2 (gratuito, corre local, 80MB)
- 384 dimensiones
- Muy rápido
- Bueno para textos académicos en inglés
"""
import structlog
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

logger = structlog.get_logger(__name__)

CHROMA_DIR = "./data/chromadb"
COLECCION_PAPERS = "papers_academicos"
MODELO_EMBEDDING = "all-MiniLM-L6-v2"


class EmbeddingService:

    def __init__(self):
        self._cliente: chromadb.Client | None = None
        self._coleccion = None
        self._modelo: SentenceTransformer | None = None

    @property
    def modelo(self) -> SentenceTransformer:
        """Carga el modelo de embeddings solo cuando se necesita."""
        if self._modelo is None:
            logger.info("cargando_modelo_embeddings", modelo=MODELO_EMBEDDING)
            self._modelo = SentenceTransformer(MODELO_EMBEDDING)
            logger.info("modelo_embeddings_listo")
        return self._modelo

    @property
    def coleccion(self):
        """Inicializa ChromaDB y la colección solo cuando se necesita."""
        if self._coleccion is None:
            Path(CHROMA_DIR).mkdir(parents=True, exist_ok=True)
            self._cliente = chromadb.PersistentClient(
                path=CHROMA_DIR,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._coleccion = self._cliente.get_or_create_collection(
                name=COLECCION_PAPERS,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "chromadb_inicializado",
                coleccion=COLECCION_PAPERS,
                total_documentos=self._coleccion.count(),
            )
        return self._coleccion

    def indexar_paper(
        self,
        doi: str,
        texto: str,
        metadata: dict,
    ) -> bool:
        """
        Genera el embedding del texto y lo guarda en ChromaDB.
        Si el DOI ya existe, actualiza el documento.

        Args:
            doi: Identificador único del paper
            texto: Abstract o texto completo del paper
            metadata: Título, autores, año, nivel_confianza, etc.

        Returns:
            True si se indexó correctamente
        """
        if not texto or not texto.strip():
            logger.warning("indexar_paper_texto_vacio", doi=doi)
            return False

        try:
            # Normalizar DOI como ID (ChromaDB no acepta "/" en IDs)
            doc_id = doi.replace("/", "_").replace(".", "_")

            # Truncar texto si es muy largo (modelo acepta hasta 512 tokens)
            texto_truncado = texto[:4000] if len(texto) > 4000 else texto

            # Generar embedding
            embedding = self.modelo.encode(texto_truncado).tolist()

            # Guardar en ChromaDB (upsert = insert o update)
            self.coleccion.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[texto_truncado],
                metadatas=[metadata],
            )

            logger.info(
                "paper_indexado",
                doi=doi,
                chars=len(texto_truncado),
                nivel_confianza=metadata.get("nivel_confianza"),
            )
            return True

        except Exception as e:
            logger.error("error_indexando_paper", doi=doi, error=str(e))
            return False

    def buscar_similares(
        self,
        texto_consulta: str,
        n_resultados: int = 3,
        filtro_doi: Optional[str] = None,
    ) -> list[dict]:
        """
        Busca los papers más similares a un texto de consulta.
        Usado en EP-003 para cruzar citas contra papers reales.

        Args:
            texto_consulta: El texto de la cita a verificar
            n_resultados: Cuántos resultados retornar
            filtro_doi: Si se especifica, solo busca en ese paper

        Returns:
            Lista de resultados con texto, metadata y score
        """
        try:
            embedding_consulta = self.modelo.encode(texto_consulta).tolist()

            where = None
            if filtro_doi:
                doi_normalizado = filtro_doi.replace("/", "_").replace(".", "_")
                where = {"doi_normalizado": doi_normalizado}

            resultados = self.coleccion.query(
                query_embeddings=[embedding_consulta],
                n_results=min(n_resultados, self.coleccion.count()),
                where=where,
                include=["documents", "metadatas", "distances"],
            )

            items = []
            documentos = resultados.get("documents", [[]])[0]
            metadatas = resultados.get("metadatas", [[]])[0]
            distancias = resultados.get("distances", [[]])[0]

            for doc, meta, dist in zip(documentos, metadatas, distancias):
                # Convertir distancia coseno a score de similitud (0-1)
                similitud = 1 - dist
                items.append({
                    "texto": doc,
                    "metadata": meta,
                    "similitud": round(similitud, 4),
                })

            logger.info(
                "busqueda_embeddings",
                consulta=texto_consulta[:50],
                resultados=len(items),
            )
            return items

        except Exception as e:
            logger.error("error_busqueda_embeddings", error=str(e))
            return []

    def paper_ya_indexado(self, doi: str) -> bool:
        """Verifica si un paper ya fue indexado (cache)."""
        try:
            doc_id = doi.replace("/", "_").replace(".", "_")
            resultado = self.coleccion.get(ids=[doc_id])
            return len(resultado["ids"]) > 0
        except Exception:
            return False

    def total_papers_indexados(self) -> int:
        """Retorna el total de papers en ChromaDB."""
        try:
            return self.coleccion.count()
        except Exception:
            return 0


# Singleton
embedding_service = EmbeddingService()
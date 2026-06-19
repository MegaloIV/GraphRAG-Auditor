"""
Almacenamiento de objetos en Supabase Storage (S3 gestionado).

Reemplaza el almacenamiento local de archivos (carpeta ./data): PDFs subidos por
el usuario y papers descargados de Unpaywall viven ahora en un bucket en la nube.

Como algunas librerías (PyMuPDF / pymupdf4llm) exigen una ruta de archivo en
disco para procesar el PDF, este servicio mantiene un cache local temporal
(determinista por clave de objeto) bajo el directorio temporal del sistema. Ese
cache es solo un detalle de implementación: la fuente de verdad es el bucket.

Layout de objetos en el bucket:
    uploads/{documento_id}.pdf      → PDF original subido por el usuario
    papers/{doi_normalizado}.txt    → texto extraído (cache) de un paper externo
"""
import tempfile
from pathlib import Path
from typing import Optional

import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)


class SupabaseStorageService:

    def __init__(self):
        self._client = None
        self._cache_dir = Path(tempfile.gettempdir()) / "graphrag_storage"

    # ── Cliente ──────────────────────────────────────────────────────────────

    def _bucket(self):
        """Retorna el handle del bucket configurado (cliente perezoso)."""
        if self._client is None:
            from supabase import create_client

            s = get_settings()
            if not s.supabase_url or not s.supabase_service_key:
                raise RuntimeError(
                    "Supabase Storage no configurado: define SUPABASE_URL y "
                    "SUPABASE_SERVICE_KEY en el .env."
                )
            self._client = create_client(s.supabase_url, s.supabase_service_key)
            logger.info("supabase_storage_conectado", url=s.supabase_url)
        return self._client.storage.from_(get_settings().supabase_storage_bucket)

    def _ruta_cache(self, ruta_objeto: str) -> Path:
        return self._cache_dir / ruta_objeto

    # ── Escritura ────────────────────────────────────────────────────────────

    def subir_bytes(
        self,
        ruta_objeto: str,
        contenido: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Sube (o sobrescribe) un objeto y deja una copia en el cache local."""
        self._bucket().upload(
            path=ruta_objeto,
            file=contenido,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        # Poblar cache local para evitar una descarga inmediata posterior.
        local = self._ruta_cache(ruta_objeto)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(contenido)
        logger.info("objeto_subido", ruta=ruta_objeto, bytes=len(contenido))
        return ruta_objeto

    def subir_texto(self, ruta_objeto: str, texto: str) -> str:
        return self.subir_bytes(
            ruta_objeto, texto.encode("utf-8"), content_type="text/plain; charset=utf-8"
        )

    # ── Lectura ──────────────────────────────────────────────────────────────

    def descargar_bytes(self, ruta_objeto: str) -> Optional[bytes]:
        """Descarga un objeto; None si no existe o falla."""
        try:
            return self._bucket().download(ruta_objeto)
        except Exception as e:
            logger.warning("objeto_no_descargado", ruta=ruta_objeto, error=str(e))
            return None

    def leer_texto(self, ruta_objeto: str) -> Optional[str]:
        data = self.descargar_bytes(ruta_objeto)
        return data.decode("utf-8") if data is not None else None

    def existe(self, ruta_objeto: str) -> bool:
        """True si el objeto existe en el bucket."""
        carpeta, _, nombre = ruta_objeto.rpartition("/")
        try:
            elementos = self._bucket().list(carpeta or None)
            return any(item.get("name") == nombre for item in elementos)
        except Exception as e:
            logger.warning("objeto_existe_error", ruta=ruta_objeto, error=str(e))
            return False

    def obtener_local(self, ruta_objeto: str) -> Optional[Path]:
        """
        Devuelve una ruta local al objeto, descargándolo del bucket si hace falta.
        Reutiliza el cache local (incluidos artefactos derivados como el .md de
        PyMuPDF que se generan junto al archivo). None si el objeto no existe.
        """
        local = self._ruta_cache(ruta_objeto)
        if local.exists():
            return local
        data = self.descargar_bytes(ruta_objeto)
        if data is None:
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        return local

    # ── Borrado ──────────────────────────────────────────────────────────────

    def eliminar(self, ruta_objeto: str) -> None:
        try:
            self._bucket().remove([ruta_objeto])
        except Exception as e:
            logger.warning("objeto_no_eliminado", ruta=ruta_objeto, error=str(e))
        local = self._ruta_cache(ruta_objeto)
        local.unlink(missing_ok=True)


storage_service = SupabaseStorageService()

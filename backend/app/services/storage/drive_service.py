"""
Copia de respaldo de los papers en Google Drive.

El sistema descarga cada PDF a un temporal, extrae su texto y borra el PDF: en
la base solo queda el markdown. Eso basta para auditar, pero deja al revisor sin
el documento original cuando quiere comprobar algo a mano, y obliga a
re-descargar —contra editoriales que bloquean— si algún día hace falta.

Este servicio sube una copia a Drive organizada por tesis. Es DELIBERADAMENTE
opcional: si no hay credenciales, o el token caducó, o no hay red, se registra
y se sigue. Una auditoría no puede depender de que Drive responda.

Configuración: ver GOOGLE_OAUTH_* en .env.example y el script
scripts/autorizar_drive.py para la autorización inicial.
"""
import io
import re
import threading
from pathlib import Path
from typing import Optional

import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# Solo lo mínimo: crear y gestionar los archivos que sube la propia app, sin
# acceso al resto del Drive del usuario.
ALCANCES = ["https://www.googleapis.com/auth/drive.file"]

_RE_NOMBRE_INVALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _nombre_seguro(texto: str, maximo: int = 120) -> str:
    """Nombre utilizable como carpeta o archivo en Drive."""
    limpio = _RE_NOMBRE_INVALIDO.sub(" ", texto or "").strip()
    limpio = re.sub(r"\s{2,}", " ", limpio)
    return (limpio[:maximo] or "sin_titulo").rstrip(". ")


class DriveService:
    """Sube papers a Drive. Inactivo mientras no haya credenciales."""

    def __init__(self):
        self._servicio = None
        self._cerrojo = threading.Lock()
        self._carpetas: dict[str, str] = {}      # cache: nombre → id de carpeta
        self._fallido = False                    # evita reintentar en cada paper

    # ── Conexión ─────────────────────────────────────────────────────────

    @property
    def activo(self) -> bool:
        """¿Hay credenciales configuradas y utilizables?"""
        return bool(settings.google_drive_carpeta_id) and not self._fallido

    def _conectar(self):
        if self._servicio is not None:
            return self._servicio
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        ruta_token = Path(settings.google_oauth_token)
        if not ruta_token.exists():
            raise RuntimeError(
                f"No hay token de Drive en {ruta_token}. "
                "Ejecuta scripts/autorizar_drive.py una vez para generarlo."
            )

        credenciales = Credentials.from_authorized_user_file(str(ruta_token), ALCANCES)
        if not credenciales.valid:
            if credenciales.expired and credenciales.refresh_token:
                credenciales.refresh(Request())
                ruta_token.write_text(credenciales.to_json(), encoding="utf-8")
                logger.info("drive_token_renovado")
            else:
                raise RuntimeError(
                    "El token de Drive caducó y no se puede renovar. Vuelve a "
                    "ejecutar scripts/autorizar_drive.py. (Mientras la app esté "
                    "en modo prueba en Google Cloud, el token expira a los 7 días.)"
                )

        self._servicio = build("drive", "v3", credentials=credenciales,
                               cache_discovery=False)
        return self._servicio

    # ── Carpetas ─────────────────────────────────────────────────────────

    def _carpeta_de_tesis(self, servicio, titulo: str) -> str:
        """Id de la subcarpeta de la tesis; la crea si no existe."""
        nombre = _nombre_seguro(titulo)
        if nombre in self._carpetas:
            return self._carpetas[nombre]

        raiz = settings.google_drive_carpeta_id
        consulta = (
            f"name = '{nombre.replace(chr(39), chr(92) + chr(39))}' "
            f"and '{raiz}' in parents "
            "and mimeType = 'application/vnd.google-apps.folder' "
            "and trashed = false"
        )
        encontradas = servicio.files().list(
            q=consulta, fields="files(id)", pageSize=1,
        ).execute().get("files", [])

        if encontradas:
            id_carpeta = encontradas[0]["id"]
        else:
            id_carpeta = servicio.files().create(
                body={
                    "name": nombre,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [raiz],
                },
                fields="id",
            ).execute()["id"]
            logger.info("drive_carpeta_creada", tesis=nombre)

        self._carpetas[nombre] = id_carpeta
        return id_carpeta

    # ── Subida ───────────────────────────────────────────────────────────

    def subir_paper(
        self,
        titulo_tesis: str,
        nombre_archivo: str,
        contenido: bytes,
        tipo_mime: str = "application/pdf",
    ) -> Optional[str]:
        """
        Sube un archivo a la carpeta de la tesis y devuelve su id, o None.

        Nunca lanza: cualquier fallo se registra y se devuelve None, porque la
        auditoría debe continuar aunque Drive no esté disponible.
        """
        if not self.activo:
            return None

        from googleapiclient.http import MediaIoBaseUpload

        try:
            with self._cerrojo:
                servicio = self._conectar()
                carpeta = self._carpeta_de_tesis(servicio, titulo_tesis)

            nombre = _nombre_seguro(nombre_archivo)
            medio = MediaIoBaseUpload(
                io.BytesIO(contenido), mimetype=tipo_mime, resumable=False
            )
            # Idempotente: si ya se subió ese paper, se reemplaza en vez de
            # acumular duplicados en cada re-auditoría.
            previos = servicio.files().list(
                q=f"name = '{nombre}' and '{carpeta}' in parents and trashed = false",
                fields="files(id)", pageSize=1,
            ).execute().get("files", [])

            if previos:
                archivo = servicio.files().update(
                    fileId=previos[0]["id"], media_body=medio, fields="id",
                ).execute()
            else:
                archivo = servicio.files().create(
                    body={"name": nombre, "parents": [carpeta]},
                    media_body=medio, fields="id",
                ).execute()

            logger.info(
                "drive_archivo_subido", tesis=_nombre_seguro(titulo_tesis)[:40],
                archivo=nombre, id=archivo["id"],
            )
            return archivo["id"]

        except Exception as e:
            # Un fallo de credenciales se repetiría en cada paper: se desactiva
            # el servicio para el resto de la ejecución y se avisa una vez.
            if "token" in str(e).lower() or "credential" in str(e).lower():
                self._fallido = True
                logger.error("drive_desactivado_por_credenciales", error=str(e)[:200])
            else:
                logger.warning("drive_subida_fallida", archivo=nombre_archivo, error=str(e)[:200])
            return None


drive_service = DriveService()

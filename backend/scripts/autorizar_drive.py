"""
Autorización inicial de Google Drive. Se ejecuta UNA vez, a mano.

Abre el navegador, pide permiso con tu cuenta y guarda el token que usará el
backend. A partir de ahí la subida es automática.

    cd backend && venv-wsl/bin/python scripts/autorizar_drive.py

Aviso: mientras la app siga en modo "prueba" en Google Cloud, el token caduca a
los 7 días —es un límite de Google, no del código— y hay que repetir esto. Para
evitarlo, publica la app en la pantalla de consentimiento (no requiere
verificación si eres el único usuario).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.storage.drive_service import ALCANCES  # noqa: E402


def main() -> int:
    settings = get_settings()
    credenciales = Path(settings.google_oauth_credenciales)
    token = Path(settings.google_oauth_token)

    if not credenciales.exists():
        print(f"ERROR: no encuentro las credenciales en {credenciales}")
        print("Descarga el JSON del ID de cliente OAuth (tipo 'Aplicación de")
        print("escritorio') desde Google Cloud y déjalo en esa ruta.")
        return 1

    print("Se abrirá el navegador para que autorices el acceso a tu Drive.")
    print("Si no se abre solo, copia la URL que aparezca aquí abajo.\n")

    flujo = InstalledAppFlow.from_client_secrets_file(str(credenciales), ALCANCES)
    # access_type=offline y prompt=consent fuerzan a que Google entregue un
    # refresh_token; sin él habría que reautorizar en cada ejecución.
    cred = flujo.run_local_server(
        port=0, access_type="offline", prompt="consent",
        authorization_prompt_message="Abre esta URL para autorizar:\n{url}",
        success_message="Listo. Ya puedes cerrar esta pestaña y volver a la terminal.",
    )

    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(cred.to_json(), encoding="utf-8")
    print(f"\nToken guardado en {token}")

    if not settings.google_drive_carpeta_id:
        print("\nFalta un paso: añade a backend/.env el id de la carpeta de Drive")
        print("donde quieres los papers:")
        print("  GOOGLE_DRIVE_CARPETA_ID=<el id que sale en la URL de la carpeta>")
        return 0

    from googleapiclient.discovery import build
    servicio = build("drive", "v3", credentials=cred, cache_discovery=False)
    carpeta = servicio.files().get(
        fileId=settings.google_drive_carpeta_id, fields="name"
    ).execute()
    print(f"Carpeta de destino verificada: «{carpeta['name']}»")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

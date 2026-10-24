from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: str = "development"
    app_port: int = 8000

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-mini"
    openai_embedding_model: str = "text-embedding-3-small"  # 1536 dimensiones

    # ── Google Drive (opcional) ──────────────────────────────────────────
    # Copia de respaldo de los papers descargados. Si google_drive_carpeta_id
    # está vacío, el sistema no sube nada y funciona exactamente igual.
    google_drive_carpeta_id: str = ""
    google_oauth_credenciales: str = "../Keys/google_oauth.json"
    google_oauth_token: str = "../Keys/google_drive_token.json"

    # CrossRef / Unpaywall
    crossref_email: str = "malcaldel1@upao.edu.pe"

    # API académica para obtener texto completo sin tropezar con los
    # antirrobots de las editoriales. Es gratuita y con cuota diaria de unas
    # 500 peticiones. Sin clave, el sistema simplemente no la usa y sigue con
    # el resto de la cascada.
    core_api_key: str = ""
    # Apagada a propósito: lo que esta API consigue se incorpora como aporte
    # externo a la auditoría, no como capacidad automática del sistema, para que la
    # comparación de cobertura entre corridas mida siempre lo mismo.
    usar_apis_academicas: bool = False

    # Neo4j
    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # Supabase (pgvector)
    supabase_db_host: str = ""
    supabase_db_port: int = 5432
    supabase_db_name: str = "postgres"
    supabase_db_user: str = "postgres"
    supabase_db_password: str = ""

    # Supabase Storage (S3 / object storage en la nube)
    supabase_url: str = ""               # URL del proyecto: https://<ref>.supabase.co
    supabase_service_key: str = ""       # service_role key (no exponer en frontend)
    supabase_storage_bucket: str = "graphrag-auditor"

    # Ingesta
    max_pdf_size_mb: int = 10

    # Logging
    log_level: str = "INFO"

    @property
    def max_pdf_size_bytes(self) -> int:
        return self.max_pdf_size_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
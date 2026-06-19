from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_env: str = "development"
    app_port: int = 8000

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"  # 1536 dimensiones

    # CrossRef / Unpaywall
    crossref_email: str = "malcaldel1@upao.edu.pe"

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

    # Ingesta
    max_pdf_size_mb: int = 10
    upload_dir: str = "./data/uploads"
    processed_dir: str = "./data/processed"

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
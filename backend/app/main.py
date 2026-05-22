from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import setup_logging, get_logger
from app.api.routes import ingesta, grafo, recuperacion, auditoria

settings = get_settings()
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Todo lo que está antes del yield se ejecuta al ARRANCAR el servidor.
    Todo lo que está después del yield se ejecuta al APAGAR el servidor.
    """
    # ── Arranque ──────────────────────────────────────────────
    logger.info("iniciando_servidor", env=settings.app_env)

    # Crear carpetas necesarias si no existen
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.processed_dir).mkdir(parents=True, exist_ok=True)
    Path("./logs").mkdir(exist_ok=True)

    # Inicializar schema de Neo4j
    if settings.neo4j_uri and settings.neo4j_password:
        try:
            from app.services.grafo.neo4j_service import neo4j_service
            neo4j_service.inicializar_schema()
        except Exception as e:
            logger.warning("neo4j_no_disponible", error=str(e))
    else:
        logger.warning("neo4j_credenciales_no_configuradas")

    yield

    # ── Apagado ───────────────────────────────────────────────
    logger.info("apagando_servidor")
    try:
        from app.services.grafo.neo4j_service import neo4j_service
        neo4j_service.cerrar()
    except Exception:
        pass


app = FastAPI(
    title="GraphRAG Auditor API",
    description="Sistema de auditoría semántica de citas y referencias bibliográficas APA 7ma edición.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS: permite que el frontend en localhost:5173 se comunique con el backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Registrar los routers
app.include_router(ingesta.router, prefix="/api/v1")
app.include_router(grafo.router, prefix="/api/v1")
app.include_router(recuperacion.router, prefix="/api/v1")
app.include_router(auditoria.router, prefix="/api/v1")


@app.get("/", tags=["Health"])
async def root():
    return {
        "nombre": "GraphRAG Auditor API",
        "version": "0.1.0",
        "estado": "operativo",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}
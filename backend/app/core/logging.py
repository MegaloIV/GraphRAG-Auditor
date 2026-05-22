import logging
import sys
from pathlib import Path
import structlog
from app.core.config import get_settings


def setup_logging() -> None:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=False),  # sin colores para archivo
        ],
    )

    # Handler consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Handler archivo
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(logs_dir / "app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [console_handler, file_handler]
    root_logger.setLevel(log_level)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str):
    return structlog.get_logger(name)
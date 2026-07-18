"""
Fixtures compartidas por las pruebas unitarias.

El documento de prueba es un PDF real: la tesis del autor del proyecto. No se
usa texto inventado en ninguna prueba — todo lo que se afirma sobre citas,
referencias, secciones o DOIs se comprueba contra ese documento.

La extracción del PDF es cara (~7 s), así que se hace una sola vez por sesión
y se comparte. El PDF se copia a un directorio temporal porque extraer_texto
escribe un .md de caché junto al original, y el repositorio no debe ensuciarse.
"""
import shutil
from pathlib import Path

import pytest

from app.services.ingesta.pdf_service import pdf_service
from app.services.grafo.extraccion_service import EntidadExtractionService

PDF_TESIS = Path(__file__).parent / "Informe de tesis_AlcaldeLavadoMatias (1).pdf"


@pytest.fixture(scope="session")
def ruta_pdf(tmp_path_factory) -> Path:
    """Copia del PDF real en un temporal, para no dejar la caché .md en el repo."""
    if not PDF_TESIS.exists():
        pytest.skip(f"No se encontró el PDF de prueba: {PDF_TESIS.name}")
    destino = tmp_path_factory.mktemp("documento") / "tesis.pdf"
    shutil.copy(PDF_TESIS, destino)
    return destino


@pytest.fixture(scope="session")
def _extraccion(ruta_pdf) -> tuple[str, int]:
    return pdf_service.extraer_texto(ruta_pdf)


@pytest.fixture(scope="session")
def texto_tesis(_extraccion) -> str:
    """Markdown completo extraído de la tesis real."""
    return _extraccion[0]


@pytest.fixture(scope="session")
def paginas_tesis(_extraccion) -> int:
    return _extraccion[1]


@pytest.fixture(scope="session")
def texto_cuerpo(texto_tesis) -> str:
    """Cuerpo de la tesis sin los anexos (como lo ve la detección de citas)."""
    return EntidadExtractionService._truncar_antes_de_anexos(texto_tesis)


@pytest.fixture(scope="session")
def seccion_referencias(texto_tesis) -> str:
    """Texto de la sección de referencias bibliográficas de la tesis real."""
    svc = EntidadExtractionService()
    match = svc._encontrar_inicio_referencias(texto_tesis)
    assert match is not None, "La tesis de prueba debe tener sección de referencias"
    desde_refs = texto_tesis[match.start():]
    return desde_refs[: svc._encontrar_fin_seccion(desde_refs)]


@pytest.fixture
def svc() -> EntidadExtractionService:
    return EntidadExtractionService()

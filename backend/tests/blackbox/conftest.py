"""
Pruebas de caja negra: el sistema se ejercita por su interfaz pública (la API
HTTP), sin conocer ni tocar sus interioridades. No se parchea nada: los
endpoints trabajan contra los servicios reales configurados en backend/.env
(Neo4j, Supabase, Storage y el LLM).

El cliente de pruebas registra cada petición y cada respuesta, de modo que la
evidencia del informe es la de la ejecución real.
"""
import json
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

PDF_TESIS = Path(__file__).parent.parent / "unit" / "Informe de tesis_AlcaldeLavadoMatias (1).pdf"
EVIDENCIA = Path(os.environ.get("EVIDENCIA_JSON", Path(__file__).parent / "evidencia.json"))
LIMITE_MB = 10
ESPERA_PIPELINE_S = 900

_evidencia: dict = {}
_actual = {"id": None}


def _corto(texto: str, limite: int = 900) -> str:
    if len(texto) > limite:
        return texto[:limite] + f"  … [{len(texto)} caracteres en total]"
    return texto


class ClienteRegistrado:
    """Cliente HTTP que anota lo que envía y lo que recibe."""

    def __init__(self, cliente: TestClient):
        self._cliente = cliente

    def request(self, metodo: str, url: str, *, descripcion_entrada: str = "", **kwargs):
        respuesta = self._cliente.request(metodo, url, **kwargs)
        try:
            cuerpo = json.dumps(respuesta.json(), ensure_ascii=False, indent=1)
        except Exception:
            cuerpo = f"<{respuesta.headers.get('content-type', 'desconocido')}, "
            cuerpo += f"{len(respuesta.content)} bytes>"

        entrada = descripcion_entrada
        if not entrada and "json" in kwargs:
            entrada = json.dumps(kwargs["json"], ensure_ascii=False)

        if _actual["id"]:
            _evidencia.setdefault(_actual["id"], {"intercambios": []})["intercambios"].append(
                {
                    "peticion": f"{metodo.upper()} {url}",
                    "cuerpo_enviado": entrada,
                    "estado": f"{respuesta.status_code} {respuesta.reason_phrase}",
                    "cuerpo_recibido": _corto(cuerpo),
                }
            )
        return respuesta

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def stream(self, metodo, url, **kw):
        return self._cliente.stream(metodo, url, **kw)

    def anotar(self, peticion: str, entrada: str, estado: str, salida: str):
        if _actual["id"]:
            _evidencia.setdefault(_actual["id"], {"intercambios": []})["intercambios"].append(
                {
                    "peticion": peticion,
                    "cuerpo_enviado": entrada,
                    "estado": estado,
                    "cuerpo_recibido": _corto(salida),
                }
            )


@pytest.fixture(scope="session")
def api():
    # raise_server_exceptions=False: un fallo no controlado del servidor debe
    # observarse como lo observaría un cliente real —una respuesta 500— y no
    # como una excepción dentro de la prueba.
    with TestClient(app, raise_server_exceptions=False) as cliente:
        yield ClienteRegistrado(cliente)


@pytest.fixture(scope="session")
def pdf_tesis() -> bytes:
    if not PDF_TESIS.exists():
        pytest.skip("No se encontró el PDF de prueba.")
    return PDF_TESIS.read_bytes()


@pytest.fixture(scope="session")
def pdf_en_el_limite() -> bytes:
    """PDF legible de exactamente el tamaño máximo admitido, para el valor límite."""
    import fitz

    documento = fitz.open()
    pagina = documento.new_page()
    pagina.insert_text((72, 72), "Documento de prueba para el valor limite de tamano.")
    pagina.insert_text((72, 96), "Contiene capa de texto, como exige el sistema.")
    minimo = documento.tobytes()
    documento.close()

    objetivo = LIMITE_MB * 1024 * 1024
    relleno = b"\n%" + b" " * (objetivo - len(minimo) - 2)  # comentario PDF: no altera el contenido
    return minimo + relleno


@pytest.fixture(scope="session")
def documento_inexistente() -> str:
    """Identificador con formato válido que no corresponde a ningún documento."""
    return str(uuid.uuid4())


@pytest.fixture(scope="session")
def documento(api, pdf_tesis) -> dict:
    """
    Carga el documento de prueba y espera a que el pipeline termine la extracción.
    Es el sujeto de las pruebas de flujo: una sola carga para toda la sesión.
    """
    respuesta = api._cliente.post(
        "/api/v1/ingesta/cargar",
        files={"archivo": ("tesis.pdf", pdf_tesis, "application/pdf")},
    )
    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    datos["_carga"] = {
        "peticion": "POST /api/v1/ingesta/cargar",
        "cuerpo_enviado": f"multipart/form-data; archivo=tesis.pdf (<{len(pdf_tesis)} bytes>)",
        "estado": f"{respuesta.status_code} {respuesta.reason_phrase}",
        "cuerpo_recibido": json.dumps(datos, ensure_ascii=False, indent=1),
    }

    documento_id = datos["documento_id"]
    eventos = []
    inicio = time.time()
    with api.stream("GET", f"/api/v1/ingesta/{documento_id}/progreso") as flujo:
        for linea in flujo.iter_lines():
            if linea.startswith("data: "):
                eventos.append(json.loads(linea[6:]))
                if eventos[-1]["estado"] in ("revision_pendiente", "completado", "error"):
                    break
            if time.time() - inicio > ESPERA_PIPELINE_S:
                pytest.fail("El pipeline no terminó dentro del tiempo previsto.")

    datos["_eventos"] = eventos
    datos["_segundos"] = round(time.time() - inicio, 1)
    assert eventos and eventos[-1]["estado"] == "revision_pendiente", eventos[-1] if eventos else "sin eventos"
    return datos


def pytest_runtest_call(item):
    _actual["id"] = item.nodeid


def pytest_runtest_logreport(report):
    if report.when == "call":
        _evidencia.setdefault(report.nodeid, {"intercambios": []})["resultado"] = report.outcome.upper()
        _actual["id"] = None


def pytest_sessionfinish(session, exitstatus):
    EVIDENCIA.write_text(json.dumps(_evidencia, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n[evidencia] {len(_evidencia)} pruebas registradas en {EVIDENCIA}")

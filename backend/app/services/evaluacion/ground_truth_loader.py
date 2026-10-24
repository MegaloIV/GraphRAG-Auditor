"""
Cargador de ground truth experto — SOLO SCAFFOLDING, SIN LÓGICA (§B3).

Propósito: obtener las etiquetas del experto (list[EtiquetaExperto]) para un
documento, de modo que POST /evaluacion/{id}/evaluar deje de recibirlas en el
body (D3) y las lea de una fuente definitiva. El mecanismo aún NO está
decidido; este módulo documenta las opciones y fija el contrato.

Opciones en evaluación (todas devuelven list[EtiquetaExperto] para un
documento_id; ninguna está implementada):

  A) CSV — un archivo `ground_truth/{documento_id}.csv` con columnas
     `cita_id, etiqueta_experto` (labels: SUPPORTS | REFUTES | NO_INFO).
     # import csv
     # with open(ruta) as f:
     #     return [EtiquetaExperto(**fila) for fila in csv.DictReader(f)]

  B) Excel — un `.xlsx` con las mismas dos columnas en la primera hoja,
     pensado para que el experto trabaje directamente en Excel.
     # import openpyxl
     # wb = openpyxl.load_workbook(ruta); ...

  C) JSON — objeto `ground_truth/{documento_id}.json` en Supabase Storage:
     `[{"cita_id": "...", "etiqueta_experto": "SUPPORTS"}, ...]`.
     # crudo = storage_service.leer_texto(f"ground_truth/{documento_id}.json")
     # return [EtiquetaExperto(**e) for e in json.loads(crudo)]

  D) Clasificación dentro del sistema — una UI donde el experto etiqueta cada
     cita; las etiquetas se persistirían (Storage o Neo4j) y este loader las
     leería de ahí.

Punto de integración: app/api/routes/evaluacion.py::evaluar — ver el
`TODO(B3)` que hoy toma las etiquetas del body del request.
"""
import structlog

from app.core.config import get_settings
from app.models.auditoria import VeredictoTipo
from app.models.evaluacion import EtiquetaExperto
from app.services.grafo.neo4j_service import neo4j_service

logger = structlog.get_logger(__name__)
settings = get_settings()

_Q_ETIQUETAS = """
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
WHERE coalesce(c.etiqueta_experto, '') <> ''
RETURN c.id AS cita_id, c.etiqueta_experto AS etiqueta
"""


def cargar(documento_id: str) -> list[EtiquetaExperto]:
    """
    Etiquetas del experto para el documento, leídas del grafo.

    Mecanismo elegido (opción D del diseño): las correcciones se capturan en el
    informe Excel —el experto rellena la columna «CLASIFICACIÓN EXPERTO»— y al
    reimportarlo quedan guardadas en cada nodo Cita. Así el ground truth no
    vive en un archivo suelto que haya que volver a adjuntar cada vez: la
    evaluación se puede recalcular cuando se quiera.

    Devuelve lista vacía si nadie ha etiquetado todavía.
    """
    try:
        with neo4j_service.driver.session(database=settings.neo4j_database) as session:
            filas = [dict(r) for r in session.run(_Q_ETIQUETAS, doc_id=documento_id)]
    except Exception as e:
        logger.error("error_cargar_ground_truth", doc_id=documento_id, error=str(e))
        return []

    etiquetas: list[EtiquetaExperto] = []
    for fila in filas:
        try:
            etiquetas.append(EtiquetaExperto(
                cita_id=fila["cita_id"],
                etiqueta_experto=VeredictoTipo(fila["etiqueta"]),
            ))
        except ValueError:
            logger.warning("etiqueta_invalida", cita_id=fila["cita_id"], valor=fila["etiqueta"])
    return etiquetas

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
from app.models.evaluacion import EtiquetaExperto


def cargar(documento_id: str) -> list[EtiquetaExperto]:
    """
    Devuelve las etiquetas del experto para el documento indicado.

    Entrada:  documento_id (str) — ID del documento auditado.
    Salida:   list[EtiquetaExperto] — una etiqueta por cita evaluada.
    Errores:  debería señalar "ground truth no disponible" para el documento
              (excepción propia o lista vacía, a definir con el mecanismo).
    """
    # TODO: definir mecanismo (opciones A/B/C/D del docstring del módulo).
    raise NotImplementedError("El mecanismo de carga del ground truth aún no está definido.")


def existe(documento_id: str) -> bool:
    """
    Indica si hay ground truth disponible para el documento (para que el
    frontend pueda mostrar u ocultar la evaluación).

    Entrada:  documento_id (str).
    Salida:   bool.
    """
    # TODO: definir mecanismo (opciones A/B/C/D del docstring del módulo).
    raise NotImplementedError("El mecanismo de carga del ground truth aún no está definido.")

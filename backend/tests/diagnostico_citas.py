"""
Script de diagnóstico para las primeras 5 citas extraídas de la tesis.
Uso: python tests/diagnostico_citas.py   (desde backend/)
"""
import sys
from pathlib import Path

# Allow imports from app/ when running from backend/
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymupdf4llm

from app.services.grafo.extraccion_service import EntidadExtractionService

PDF_PATH = Path(__file__).parent / "Informe de tesis_AlcaldeLavadoMatias.pdf"
NUM_CITAS = 5


def main() -> None:
    if not PDF_PATH.exists():
        print(f"ERROR: PDF no encontrado en {PDF_PATH}", file=sys.stderr)
        sys.exit(1)

    print("Cargando PDF…")
    texto_md = pymupdf4llm.to_markdown(str(PDF_PATH))

    service = EntidadExtractionService()

    texto_truncado = service._truncar_antes_de_anexos(texto_md)
    num_paginas = texto_md.count("\n-----\n") + 1

    print(f"Texto tras truncar anexos: {len(texto_truncado)} chars | páginas estimadas: {num_paginas}")
    print("Extrayendo citas (incluye llamada al LLM)…\n")

    citas = service.extraer_citas(texto_truncado, num_paginas)

    muestra = citas[:NUM_CITAS]
    sep = "═" * 50

    for i, cita in enumerate(muestra, start=1):
        print(sep)
        print(f"CITA {i}")
        print(sep)
        print(f"texto_cita    : {cita.texto_cita}")
        print(f"tipo          : {cita.tipo.value.upper()}")
        print(f"pagina        : {cita.pagina}")
        fragmento = cita.fragmento_oracion or ""
        print(f'fragmento     : "{fragmento}"')
        print(f"largo_frag    : {len(fragmento)} caracteres")
        print(sep)
        print()

    if not muestra:
        print("No se extrajeron citas.")


if __name__ == "__main__":
    main()

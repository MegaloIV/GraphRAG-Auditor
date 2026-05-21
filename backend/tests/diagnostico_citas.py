"""
Script de diagnóstico para las primeras 5 citas extraídas de la tesis.
Uso: python tests/diagnostico_citas.py   (desde backend/)
"""
import sys
from pathlib import Path

# Allow imports from app/ when running from backend/
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymupdf4llm

import app.services.grafo.extraccion_service as extraccion_mod
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

    # Interceptar completar() para capturar el valor raw de "tipo" que devuelve
    # el LLM, antes de que el servicio lo mapee a TipoCita.
    tipo_llm_por_cita: dict[str, str] = {}
    completar_original = extraccion_mod.llm_service.completar

    def completar_interceptado(system_prompt: str, user_prompt: str, **kwargs) -> str:
        respuesta = completar_original(system_prompt, user_prompt, **kwargs)
        for cita_raw in service._parsear_json(respuesta):
            texto = cita_raw.get("texto_cita", "")
            # "AUSENTE" deja claro que el LLM no incluyó el campo en absoluto
            tipo = cita_raw.get("tipo", "AUSENTE")
            if texto:
                tipo_llm_por_cita[texto] = tipo
        return respuesta

    extraccion_mod.llm_service.completar = completar_interceptado
    try:
        citas = service.extraer_citas(texto_truncado, num_paginas)
    finally:
        extraccion_mod.llm_service.completar = completar_original

    muestra = citas[:NUM_CITAS]
    sep = "═" * 50

    for i, cita in enumerate(muestra, start=1):
        tipo_llm   = tipo_llm_por_cita.get(cita.texto_cita, "AUSENTE")
        tipo_final = cita.tipo.value.upper()
        print(sep)
        print(f"CITA {i}")
        print(sep)
        print(f"texto_cita    : {cita.texto_cita}")
        print(f"tipo_llm      : {tipo_llm}")
        print(f"tipo_final    : {tipo_final}")
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

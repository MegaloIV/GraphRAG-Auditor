"""
Script de depuración: convierte un PDF a Markdown con pymupdf4llm
y muestra cómo detecta (o no) la sección de referencias.

Uso:
    python debug_pdf_to_md.py <ruta_al_pdf>

Genera:
    <nombre_pdf>.md  — texto extraído completo
    Imprime en consola las líneas candidatas a encabezado y el resultado
    de la detección de referencias.
"""

import re
import sys
from pathlib import Path


# ── Mismos regex que usa extraccion_service ──────────────────────────────────

_RE_REFERENCIAS = re.compile(
    r'^(\*{1,2})?\s*#{0,3}\s*'
    r'(lista\s+de\s+referencias?\s*(?:bibliogr[áa]ficas?)?'
    r'|referencias?\s*bibliogr[áa]ficas?|referencias?|bibliograf[íi]a|references?'
    r'|reference\s+list'
    r'|works?\s+cited|fuentes?\s+bibliogr[áa]ficas?|fuentes?\s+de\s+consulta'
    r'|\d+(?:\.\d+)*\.?\s+referencias?(?:\s+bibliogr[áa]ficas?)?|\d+(?:\.\d+)*\.?\s+bibliograf[íi]a)'
    r'\s*:?\s*(\*{1,2})?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

_RE_REFERENCIAS_NUMERADA = re.compile(
    r'^\d+(?:\.\d+)*\.?\s+(?:referencias?(?:\s+\w+)?|bibliograf[íi]a(?:\s+\w+)?)\s*:?\s*$',
    re.IGNORECASE | re.MULTILINE,
)

_RE_INICIO_ANEXOS = re.compile(
    r'^(?:#{1,3}\s+)?(?:\*{1,2})?\s*'
    r'(Anexos?|Ap[eé]ndices?|Appendix)'
    r'\s*(?:\*{1,2})?\s*$',
    re.IGNORECASE | re.MULTILINE,
)


def truncar_antes_de_anexos(texto: str) -> str:
    posicion = len(texto)
    m = _RE_INICIO_ANEXOS.search(texto)
    if m:
        posicion = m.start()
    limite_70 = int(len(texto) * 0.70)
    for m_num in re.finditer(r'^Anexo\s+\d+[\.\:]', texto, re.MULTILINE):
        if m_num.start() >= limite_70:
            posicion = min(posicion, m_num.start())
            break
    return texto[:posicion]


def encontrar_inicio_referencias(texto: str):
    m = _RE_REFERENCIAS.search(texto)
    if not m:
        m = _RE_REFERENCIAS_NUMERADA.search(texto)
    return m


def main():
    if len(sys.argv) < 2:
        print("Uso: python debug_pdf_to_md.py <ruta_al_pdf>")
        sys.exit(1)

    ruta_pdf = Path(sys.argv[1])
    if not ruta_pdf.exists():
        print(f"Archivo no encontrado: {ruta_pdf}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"PDF: {ruta_pdf.name}")
    print(f"{'='*60}\n")

    # 1. Extraer con pymupdf4llm (igual que el flujo principal)
    import fitz
    import pymupdf4llm

    doc = fitz.open(str(ruta_pdf))
    num_paginas = len(doc)
    doc.close()
    print(f"Páginas: {num_paginas}")

    print("Extrayendo texto con pymupdf4llm...")
    texto_md = pymupdf4llm.to_markdown(str(ruta_pdf))
    print(f"Caracteres extraídos: {len(texto_md)}\n")

    # 2. Guardar .md completo
    ruta_md = ruta_pdf.with_suffix(".md")
    ruta_md.write_text(texto_md, encoding="utf-8")
    print(f"Markdown guardado en: {ruta_md}\n")

    # 3. Mostrar las primeras líneas no vacías (estructura inicial)
    print(f"{'─'*60}")
    print("PRIMERAS 30 LÍNEAS NO VACÍAS:")
    print(f"{'─'*60}")
    lineas_no_vacias = [l for l in texto_md.splitlines() if l.strip()][:30]
    for i, l in enumerate(lineas_no_vacias, 1):
        print(f"  {i:>3}: {repr(l)}")

    # 4. Mostrar últimas líneas (zona donde suelen estar las referencias)
    print(f"\n{'─'*60}")
    print("ÚLTIMAS 60 LÍNEAS NO VACÍAS:")
    print(f"{'─'*60}")
    lineas_no_vacias_todas = [l for l in texto_md.splitlines() if l.strip()]
    for i, l in enumerate(lineas_no_vacias_todas[-60:], len(lineas_no_vacias_todas) - 59):
        print(f"  {i:>4}: {repr(l)}")

    # 5. Buscar todas las líneas que parecen encabezados de sección
    print(f"\n{'─'*60}")
    print("LÍNEAS QUE PARECEN ENCABEZADOS (con # o ** o numeradas):")
    print(f"{'─'*60}")
    _RE_HEADING = re.compile(r'^(?:#{1,3}\s|\*{1,2}.+\*{1,2}$|\d+(?:\.\d+)*\.?\s)')
    for i, linea in enumerate(texto_md.splitlines(), 1):
        if _RE_HEADING.match(linea.strip()):
            print(f"  línea {i:>4}: {repr(linea.strip())}")

    # 6. Detección de sección de referencias
    print(f"\n{'─'*60}")
    print("DETECCIÓN DE SECCIÓN DE REFERENCIAS:")
    print(f"{'─'*60}")

    texto_sin_anexos = truncar_antes_de_anexos(texto_md)
    if len(texto_sin_anexos) < len(texto_md):
        print(f"  Anexos detectados: se descartaron {len(texto_md) - len(texto_sin_anexos)} chars al final\n")

    match = encontrar_inicio_referencias(texto_sin_anexos)
    if match:
        linea_match = texto_sin_anexos[:match.start()].count('\n') + 1
        print(f"  ✅ ENCONTRADA en línea ~{linea_match}")
        print(f"  Encabezado detectado: {repr(match.group().strip())}")
        print(f"  Caracteres desde el encabezado: {len(texto_sin_anexos) - match.start()}")
        print(f"\n  Primeras 5 líneas de la sección de referencias:")
        sec = texto_sin_anexos[match.start():]
        for l in [l for l in sec.splitlines() if l.strip()][:5]:
            print(f"    {repr(l)}")
    else:
        print("  ❌ NO ENCONTRADA — se usará el fallback (últimos 12000 chars)")
        print("\n  Líneas candidatas que NO hicieron match (últimas 80 líneas):")
        ultimas = texto_sin_anexos.splitlines()[-80:]
        for l in ultimas:
            if l.strip():
                print(f"    {repr(l)}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()

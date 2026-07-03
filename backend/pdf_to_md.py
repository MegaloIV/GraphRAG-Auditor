import sys
from pathlib import Path
import pymupdf4llm

ruta_pdf = Path(sys.argv[1])
texto_md = pymupdf4llm.to_markdown(str(ruta_pdf))
ruta_md = ruta_pdf.with_suffix(".md")
ruta_md.write_text(texto_md, encoding="utf-8")
print(f"Guardado: {ruta_md}")

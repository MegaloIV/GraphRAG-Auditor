"""
Diagnóstico de ChromaDB: imprime los primeros 3 documentos
con su document_id, primeros 200 caracteres de texto y metadata.

Uso: python tests/diagnostico_chroma.py
"""
import sys
from pathlib import Path

# Permite ejecutar desde backend/ o desde la raíz del proyecto
sys.path.insert(0, str(Path(__file__).parent.parent))

import chromadb
from chromadb.config import Settings as ChromaSettings

CHROMA_DIR = "./data/chromadb"
COLECCION = "papers_academicos"


def main():
    cliente = chromadb.PersistentClient(
        path=CHROMA_DIR,
        settings=ChromaSettings(anonymized_telemetry=False),
    )

    coleccion = cliente.get_or_create_collection(
        name=COLECCION,
        metadata={"hnsw:space": "cosine"},
    )

    total = coleccion.count()
    print(f"Total de chunks en ChromaDB: {total}\n")

    if total == 0:
        print("La colección está vacía.")
        return

    resultado = coleccion.get(
        limit=3,
        include=["documents", "metadatas"],
    )

    ids       = resultado.get("ids", [])
    documentos = resultado.get("documents", [])
    metadatas  = resultado.get("metadatas", [])

    for i, (doc_id, texto, meta) in enumerate(zip(ids, documentos, metadatas), 1):
        print(f"{'─' * 60}")
        print(f"[{i}] document_id : {doc_id}")
        print(f"    texto (200c): {repr(texto[:200])}")
        print(f"    metadata    :")
        for k, v in meta.items():
            print(f"      {k}: {v}")
        print()


if __name__ == "__main__":
    main()

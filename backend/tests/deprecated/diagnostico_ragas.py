"""
Diagnóstico: por qué evaluar-ragas retorna total_evaluadas=0.

Ejecutar desde backend/:
    python tests/diagnostico_ragas.py <documento_id>

Si no se pasa documento_id, busca el primero en Neo4j.
"""
import sys
import io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import get_settings
from app.services.grafo.neo4j_service import neo4j_service
from app.services.vectorstore.supabase_service import supabase_vector_service

settings = get_settings()
SEP = "─" * 60


def run(doc_id: str | None = None):
    print(SEP)
    print("1. DOCUMENTOS EN NEO4J")
    print(SEP)
    with neo4j_service.driver.session(database=settings.neo4j_database) as session:
        docs = list(session.run("MATCH (d:Documento) RETURN d.id AS id, d.nombre_archivo AS nombre"))

    if not docs:
        print("  ❌  No hay documentos en Neo4j. Ingesta pendiente.")
        return

    for d in docs:
        print(f"  id={d['id']}  nombre={d['nombre']}")

    if doc_id is None:
        doc_id = docs[0]["id"]
        print(f"\n  → Usando doc_id='{doc_id}' (primero encontrado)")

    print()
    print(SEP)
    print("2. PRIMERAS 10 CITAS — campos relevantes para RAGAS")
    print(SEP)
    query_citas = """
    MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita)
    RETURN c.id, c.texto, c.fragmento, c.pagina_paper,
           c.veredicto, c.fragmento_evidencia
    LIMIT 10
    """
    with neo4j_service.driver.session(database=settings.neo4j_database) as session:
        citas = list(session.run(query_citas, doc_id=doc_id))

    if not citas:
        print("  ❌  No hay citas para este documento.")
    else:
        for c in citas:
            frag_ev = c["c.fragmento_evidencia"]
            frag_or = c["c.fragmento"]
            print(f"  cita_id         : {c['c.id']}")
            print(f"  texto           : {(c['c.texto'] or '')[:80]!r}")
            print(f"  veredicto       : {c['c.veredicto']}")
            print(f"  fragmento       : {repr((frag_or or '')[:60]) if frag_or is not None else 'NULL'}")
            print(f"  fragmento_evid  : {repr((frag_ev or '')[:60]) if frag_ev is not None else 'NULL'}")
            print(f"  pagina_paper    : {c['c.pagina_paper']}")
            print()

    print(SEP)
    print("3. CONTEOS GLOBALES — filtros que usa _Q_LEER_CITAS de RAGAS")
    print(SEP)
    with neo4j_service.driver.session(database=settings.neo4j_database) as session:
        def count(q):
            return session.run(q, doc_id=doc_id).single()[0]

        total_citas = count(
            "MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita) RETURN count(c)"
        )
        con_fragmento = count(
            "MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita) "
            "WHERE c.fragmento IS NOT NULL RETURN count(c)"
        )
        con_fragmento_no_vacio = count(
            "MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita) "
            "WHERE c.fragmento IS NOT NULL AND c.fragmento <> '' RETURN count(c)"
        )
        con_evidencia = count(
            "MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita) "
            "WHERE c.fragmento_evidencia IS NOT NULL "
            "  AND c.fragmento_evidencia <> '' RETURN count(c)"
        )
        con_justificacion = count(
            "MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita) "
            "WHERE c.justificacion IS NOT NULL RETURN count(c)"
        )
        pasarian_filtro = count(
            "MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita) "
            "WHERE c.fragmento IS NOT NULL "
            "  AND c.fragmento_evidencia IS NOT NULL "
            "  AND c.fragmento_evidencia <> '' "
            "  AND c.justificacion IS NOT NULL RETURN count(c)"
        )

    print(f"  total citas                      : {total_citas}")
    print(f"  c.fragmento IS NOT NULL          : {con_fragmento}")
    print(f"  c.fragmento != ''                : {con_fragmento_no_vacio}")
    print(f"  c.fragmento_evidencia != ''      : {con_evidencia}  ← CLAVE: esto decide si RAGAS evalúa")
    print(f"  c.justificacion IS NOT NULL      : {con_justificacion}")
    print(f"  PASAN EL FILTRO RAGAS COMPLETO   : {pasarian_filtro}  ← debe ser > 0 para que funcione")

    print()
    print(SEP)
    print("4. VEREDICTOS — distribución")
    print(SEP)
    with neo4j_service.driver.session(database=settings.neo4j_database) as session:
        veredictos = list(session.run(
            "MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c:Cita) "
            "WHERE c.veredicto IS NOT NULL "
            "RETURN c.veredicto AS v, count(*) AS n ORDER BY n DESC",
            doc_id=doc_id,
        ))
    if not veredictos:
        print("  ⚠️  Ninguna cita tiene veredicto (auditoría no ejecutada todavía)")
    for v in veredictos:
        print(f"  {v['v']:<20}: {v['n']}")

    print()
    print(SEP)
    print("5. SUPABASE — chunks indexados")
    print(SEP)
    total_chunks = supabase_vector_service.total_chunks()
    print(f"  total chunks en papers_chunks    : {total_chunks}")
    if total_chunks == 0:
        print("  ❌  Sin chunks: los papers referenciados no están indexados.")
        print("     → Ejecuta el pipeline de descarga + indexación de papers.")

    print()
    print(SEP)
    print("6. REFERENCIAS CON doi_verificado — si no están en Supabase, el audit retorna NO_VERIFICABLE")
    print(SEP)
    with neo4j_service.driver.session(database=settings.neo4j_database) as session:
        refs = list(session.run(
            "MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia) "
            "RETURN r.id AS rid, r.doi_verificado AS doi_v, r.doi AS doi_raw, r.titulo AS titulo "
            "LIMIT 10",
            doc_id=doc_id,
        ))
    for r in refs:
        en_supabase = False
        if r["doi_v"]:
            doi_norm = r["doi_v"].replace("/", "_").replace(".", "_")
            en_supabase = supabase_vector_service.paper_ya_indexado(doi_norm)
        print(
            f"  doi_verificado={r['doi_v'] or 'None':<30} "
            f"en_supabase={en_supabase}  titulo={str(r['titulo'])[:40]}"
        )

    print()
    print(SEP)
    print("DIAGNÓSTICO FINAL")
    print(SEP)
    if pasarian_filtro == 0:
        if total_chunks == 0:
            print("  CAUSA: Supabase no tiene ningún chunk indexado.")
            print("  ACCIÓN: indexa los PDFs de los papers citados antes de auditar.")
        elif con_evidencia == 0:
            print("  CAUSA: fragmento_evidencia='' en todas las citas.")
            if veredictos and all(v["v"] == "NO_VERIFICABLE" for v in veredictos):
                print("  Sub-causa: auditoría resultó en 100% NO_VERIFICABLE.")
                print("  → Papers con doi_verificado no están en Supabase, o retrieval falla.")
            else:
                print("  → Auditoría no se ejecutó, o fragmento_evidencia no se persistió.")
        else:
            print("  CAUSA: otros filtros de la query RAGAS están descartando las citas.")
    else:
        print(f"  OK: {pasarian_filtro} cita(s) pasarían el filtro. ¿El endpoint aún retorna 0?")
        print("  Revisa que el doc_id que pasas al endpoint coincida con el usado aquí.")


if __name__ == "__main__":
    arg_doc_id = sys.argv[1] if len(sys.argv) > 1 else None
    run(arg_doc_id)

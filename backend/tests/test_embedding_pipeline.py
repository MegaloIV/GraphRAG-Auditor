"""
Test aislado del pipeline de embeddings.

Replica exactamente el flujo:
    carga de modelo → generación de embedding → conexión Supabase → inserción → consulta

SIN importar ningún módulo del proyecto (evita caché de .pyc)
USA print() directamente, NO el sistema de logging del proyecto
Cada paso tiene try/except explícito para mostrar exactamente dónde falla.

Ejecutar desde el directorio backend/:
    python test_embedding_pipeline.py
"""
import sys
import os

# ──────────────────────────────────────────────────────────────────────────────
# PASO 0 — Leer .env manualmente (sin pydantic-settings, sin caché de Settings)
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 0 — Leyendo .env manualmente")
print("="*70)

env_vars = {}
env_path = os.path.join(os.path.dirname(__file__), ".env")

try:
    with open(env_path, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            env_vars[clave.strip()] = valor.strip().strip('"').strip("'")
    print(f"  [OK] .env leído: {len(env_vars)} variables encontradas")
except FileNotFoundError:
    print(f"  [ERROR] No se encontró .env en: {env_path}")
    print("          Crea el archivo .env con las variables de Supabase.")
    sys.exit(1)

# Extraer credenciales de Supabase
SUPABASE_HOST = env_vars.get("SUPABASE_DB_HOST", "")
SUPABASE_PORT = int(env_vars.get("SUPABASE_DB_PORT", "5432"))
SUPABASE_DB   = env_vars.get("SUPABASE_DB_NAME", "postgres")
SUPABASE_USER = env_vars.get("SUPABASE_DB_USER", "postgres")
SUPABASE_PASS = env_vars.get("SUPABASE_DB_PASSWORD", "")

print(f"  HOST : {SUPABASE_HOST or '(vacío)'}")
print(f"  PORT : {SUPABASE_PORT}")
print(f"  DB   : {SUPABASE_DB}")
print(f"  USER : {SUPABASE_USER}")
print(f"  PASS : {'(configurado)' if SUPABASE_PASS else '(VACÍO — esto causará fallo de conexión)'}")

if not SUPABASE_HOST:
    print("\n  [ERROR] SUPABASE_DB_HOST está vacío en .env")
    print("          Sin host no hay conexión posible.")
    sys.exit(1)

if not SUPABASE_PASS:
    print("\n  [ADVERTENCIA] SUPABASE_DB_PASSWORD está vacío — la conexión probablemente fallará")

# ──────────────────────────────────────────────────────────────────────────────
# PASO 1 — Importar sentence-transformers y cargar el modelo
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 1 — Cargando modelo sentence-transformers")
print("="*70)

MODELO_EMBEDDING = "all-MiniLM-L6-v2"

try:
    print(f"  Importando sentence_transformers...", end=" ", flush=True)
    from sentence_transformers import SentenceTransformer
    print("OK")
except ImportError as e:
    print(f"\n  [ERROR] No se pudo importar sentence_transformers: {e}")
    print("          Instala con: pip install sentence-transformers")
    sys.exit(1)

try:
    print(f"  Cargando modelo '{MODELO_EMBEDDING}'...", end=" ", flush=True)
    modelo = SentenceTransformer(MODELO_EMBEDDING)
    print("OK")
    print(f"  Dimensiones del modelo: {modelo.get_sentence_embedding_dimension()}")
except Exception as e:
    print(f"\n  [ERROR] No se pudo cargar el modelo: {e}")
    print("          ¿Hay conexión a internet para descargar? ¿Hay espacio en disco?")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────────────────
# PASO 2 — Generar un embedding de prueba
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 2 — Generando embedding de prueba")
print("="*70)

TEXTO_PRUEBA = "This is a test sentence for embedding generation in the GraphRAG Auditor."

try:
    print(f"  Texto: '{TEXTO_PRUEBA[:60]}...'")
    print(f"  Codificando...", end=" ", flush=True)
    embedding_raw = modelo.encode(TEXTO_PRUEBA)
    print("OK")
    print(f"  Tipo del resultado: {type(embedding_raw)}")
    print(f"  Shape: {embedding_raw.shape}")
    print(f"  Dtype: {embedding_raw.dtype}")

    # Convertir a lista Python (como hace el código original)
    embedding_lista = embedding_raw.tolist()
    print(f"  embedding.tolist() → list[float], len={len(embedding_lista)}")
    print(f"  Primeros 5 valores: {[round(v, 6) for v in embedding_lista[:5]]}")

except Exception as e:
    print(f"\n  [ERROR] Falló la generación de embedding: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────────────────
# PASO 3 — Importar dependencias de DB
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 3 — Importando dependencias de base de datos")
print("="*70)

try:
    print("  Importando psycopg2...", end=" ", flush=True)
    import psycopg2
    import psycopg2.extras
    print("OK")
except ImportError as e:
    print(f"\n  [ERROR] No se pudo importar psycopg2: {e}")
    print("          Instala con: pip install psycopg2-binary")
    sys.exit(1)

try:
    print("  Importando pgvector.psycopg2...", end=" ", flush=True)
    from pgvector.psycopg2 import register_vector
    print("OK")
except ImportError as e:
    print(f"\n  [ERROR] No se pudo importar pgvector: {e}")
    print("          Instala con: pip install pgvector")
    sys.exit(1)

try:
    print("  Importando numpy...", end=" ", flush=True)
    import numpy as np
    print("OK")
except ImportError as e:
    print(f"\n  [ERROR] No se pudo importar numpy: {e}")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────────────────
# PASO 4 — Conectar a Supabase
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 4 — Conectando a Supabase (psycopg2 directo)")
print("="*70)

conn = None
try:
    print(f"  Conectando a {SUPABASE_HOST}:{SUPABASE_PORT}/{SUPABASE_DB}...", end=" ", flush=True)
    conn = psycopg2.connect(
        host=SUPABASE_HOST,
        port=SUPABASE_PORT,
        dbname=SUPABASE_DB,
        user=SUPABASE_USER,
        password=SUPABASE_PASS,
        connect_timeout=15,
        sslmode="require",
    )
    register_vector(conn)
    print("OK")
    print(f"  Conexión establecida. Estado: {conn.status}")

except psycopg2.OperationalError as e:
    print(f"\n  [ERROR] No se pudo conectar a Supabase:")
    print(f"          {e}")
    print("\n  Posibles causas:")
    print("    - Contraseña incorrecta o vacía en .env")
    print("    - Host/puerto incorrecto")
    print("    - IP no autorizada (Supabase bloquea por IP)")
    print("    - Para pooler de Supabase usar puerto 6543 en modo transaction")
    sys.exit(1)
except Exception as e:
    print(f"\n  [ERROR] Error inesperado al conectar: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────────────────
# PASO 5 — Verificar que la tabla papers_chunks existe
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 5 — Verificando tabla papers_chunks")
print("="*70)

try:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'papers_chunks'
            ORDER BY ordinal_position
        """)
        columnas = cur.fetchall()

    if not columnas:
        print("  [ERROR] La tabla 'papers_chunks' NO existe en la base de datos.")
        print("          Necesitas crear la tabla primero.")
        print("""
  SQL para crear la tabla:
  -----------------------
  CREATE EXTENSION IF NOT EXISTS vector;

  CREATE TABLE papers_chunks (
      id               TEXT PRIMARY KEY,
      doi              TEXT,
      doi_normalizado  TEXT,
      titulo           TEXT,
      anio             TEXT,
      pagina           INTEGER,
      chunk_index      INTEGER,
      nivel_confianza  TEXT,
      referencia_id    TEXT,
      contenido        TEXT,
      embedding        vector(384)
  );

  CREATE INDEX ON papers_chunks USING ivfflat (embedding vector_cosine_ops)
      WITH (lists = 100);
  -----------------------
        """)
        conn.close()
        sys.exit(1)

    print(f"  [OK] Tabla encontrada con {len(columnas)} columnas:")
    for col_name, col_type in columnas:
        print(f"       {col_name:<20} {col_type}")

except Exception as e:
    print(f"  [ERROR] No se pudo verificar la tabla: {e}")
    import traceback; traceback.print_exc()
    conn.close()
    sys.exit(1)

# Contar registros existentes
try:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM papers_chunks")
        total = cur.fetchone()[0]
    print(f"\n  Registros actuales en papers_chunks: {total}")
except Exception as e:
    print(f"  [ADVERTENCIA] No se pudo contar registros: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# PASO 6A — Insertar chunk de prueba con embedding como Python list (método actual)
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 6A — INSERT con embedding como Python list (método actual del código)")
print("="*70)

TEST_ID = "test_diagnostico_chunk_0"
TEST_DOI = "10.9999/test.diagnostico"
TEST_DOI_NORM = "10_9999_test_diagnostico"

# Limpiar cualquier registro de prueba anterior
try:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM papers_chunks WHERE doi_normalizado = %s", (TEST_DOI_NORM,))
    conn.commit()
    print("  Registros de prueba anteriores limpiados.")
except Exception as e:
    print(f"  [ADVERTENCIA] No se limpió el registro anterior: {e}")
    conn.rollback()

try:
    registro = {
        "id":              TEST_ID,
        "doi":             TEST_DOI,
        "doi_normalizado": TEST_DOI_NORM,
        "titulo":          "Paper de prueba — diagnóstico embedding pipeline",
        "anio":            "2026",
        "pagina":          1,
        "chunk_index":     0,
        "nivel_confianza": "test",
        "referencia_id":   "ref_test_001",
        "contenido":       TEXTO_PRUEBA,
        "embedding":       embedding_lista,   # <-- Python list, igual que el código original
    }

    print(f"  Tipo de embedding en el registro: {type(registro['embedding'])}")
    print(f"  Intentando INSERT con Python list...", end=" ", flush=True)

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO papers_chunks
                (id, doi, doi_normalizado, titulo, anio, pagina,
                 chunk_index, nivel_confianza, referencia_id,
                 contenido, embedding)
            VALUES
                (%(id)s, %(doi)s, %(doi_normalizado)s, %(titulo)s,
                 %(anio)s, %(pagina)s, %(chunk_index)s,
                 %(nivel_confianza)s, %(referencia_id)s,
                 %(contenido)s, %(embedding)s)
            ON CONFLICT (id) DO UPDATE SET
                contenido = EXCLUDED.contenido,
                embedding = EXCLUDED.embedding
            """,
            [registro],
        )
    conn.commit()
    print("OK — Python list funciona correctamente")
    INSERT_LIST_OK = True

except Exception as e:
    print(f"\n  [ERROR] INSERT con Python list FALLÓ: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    conn.rollback()
    INSERT_LIST_OK = False

# ──────────────────────────────────────────────────────────────────────────────
# PASO 6B — INSERT con numpy array (método corregido)
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 6B — INSERT con embedding como numpy.ndarray (método corregido)")
print("="*70)

try:
    embedding_numpy = np.array(embedding_lista, dtype=np.float32)
    registro_np = {**registro, "id": TEST_ID + "_np", "embedding": embedding_numpy}

    print(f"  Tipo de embedding en el registro: {type(registro_np['embedding'])}")
    print(f"  Shape: {embedding_numpy.shape}, dtype: {embedding_numpy.dtype}")
    print(f"  Intentando INSERT con numpy array...", end=" ", flush=True)

    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO papers_chunks
                (id, doi, doi_normalizado, titulo, anio, pagina,
                 chunk_index, nivel_confianza, referencia_id,
                 contenido, embedding)
            VALUES
                (%(id)s, %(doi)s, %(doi_normalizado)s, %(titulo)s,
                 %(anio)s, %(pagina)s, %(chunk_index)s,
                 %(nivel_confianza)s, %(referencia_id)s,
                 %(contenido)s, %(embedding)s)
            ON CONFLICT (id) DO UPDATE SET
                contenido = EXCLUDED.contenido,
                embedding = EXCLUDED.embedding
            """,
            [registro_np],
        )
    conn.commit()
    print("OK — numpy array funciona correctamente")
    INSERT_NUMPY_OK = True

except Exception as e:
    print(f"\n  [ERROR] INSERT con numpy array FALLÓ: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    conn.rollback()
    INSERT_NUMPY_OK = False

# ──────────────────────────────────────────────────────────────────────────────
# PASO 7 — Consulta de similitud (búsqueda vectorial)
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 7 — Búsqueda semántica (cosine similarity)")
print("="*70)

TEXTO_CONSULTA = "sentence embedding test for academic paper auditing"

try:
    print(f"  Consulta: '{TEXTO_CONSULTA}'")
    print(f"  Generando embedding de consulta...", end=" ", flush=True)
    vec_consulta = np.array(modelo.encode(TEXTO_CONSULTA), dtype=np.float32)
    print("OK")

    print(f"  Ejecutando búsqueda TOP-3...", end=" ", flush=True)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, contenido, doi,
                   1 - (embedding <=> %s) AS similitud
            FROM papers_chunks
            WHERE doi_normalizado = %s
            ORDER BY embedding <=> %s
            LIMIT 3
        """, (vec_consulta, TEST_DOI_NORM, vec_consulta))
        resultados = [dict(r) for r in cur.fetchall()]
    print("OK")

    print(f"\n  Resultados encontrados: {len(resultados)}")
    for i, r in enumerate(resultados):
        print(f"  [{i+1}] id={r['id']}")
        print(f"       similitud={r['similitud']:.6f}")
        print(f"       contenido={r['contenido'][:80]}...")

    if not resultados:
        print("  [ADVERTENCIA] No se encontraron resultados.")
        print("  Puede que la inserción de los pasos 6A/6B haya fallado.")

except Exception as e:
    print(f"\n  [ERROR] Búsqueda semántica FALLÓ: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()

# ──────────────────────────────────────────────────────────────────────────────
# PASO 8 — Verificar paper_ya_indexado (lógica de caché de Supabase)
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 8 — Verificando lógica de paper_ya_indexado (caché en Supabase)")
print("="*70)

try:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM papers_chunks WHERE doi_normalizado = %s",
            (TEST_DOI_NORM,)
        )
        count = cur.fetchone()[0]

    ya_indexado = count > 0
    print(f"  doi_normalizado buscado: '{TEST_DOI_NORM}'")
    print(f"  Chunks encontrados: {count}")
    print(f"  paper_ya_indexado() retornaría: {ya_indexado}")

    if ya_indexado:
        print("\n  [IMPORTANTE] Si este DOI estuviera en producción,")
        print("               verificacion_service SALTARÍA la re-indexación.")
        print("               Para forzar re-index hay que DELETE primero.")

except Exception as e:
    print(f"\n  [ERROR] Falló la verificación de indexado: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# PASO 9 — Limpiar datos de prueba
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("PASO 9 — Limpiando datos de prueba")
print("="*70)

try:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM papers_chunks WHERE doi_normalizado = %s", (TEST_DOI_NORM,))
        deleted = cur.rowcount
    conn.commit()
    print(f"  [OK] {deleted} registro(s) de prueba eliminados.")
except Exception as e:
    print(f"  [ERROR] No se pudo limpiar: {e}")
    conn.rollback()

# ──────────────────────────────────────────────────────────────────────────────
# RESUMEN FINAL
# ──────────────────────────────────────────────────────────────────────────────
print("\n" + "="*70)
print("RESUMEN DE DIAGNÓSTICO")
print("="*70)

print(f"\n  ✓ Modelo cargado:             all-MiniLM-L6-v2 (384 dims)")
print(f"  ✓ Embedding generado:         list[float], len=384")
print(f"  {'✓' if INSERT_LIST_OK else '✗'} INSERT con Python list:       {'OK' if INSERT_LIST_OK else 'FALLO'}")
print(f"  {'✓' if INSERT_NUMPY_OK else '✗'} INSERT con numpy array:      {'OK' if INSERT_NUMPY_OK else 'FALLO'}")

if not INSERT_LIST_OK and INSERT_NUMPY_OK:
    print("""
  ┌─────────────────────────────────────────────────────────────────┐
  │ BUG CONFIRMADO: El código original usa Python list para el      │
  │ embedding en indexar_chunks(), pero pgvector necesita           │
  │ numpy.ndarray. Esto causa que todos los INSERTs fallen.         │
  │                                                                  │
  │ SOLUCIÓN: En embedding_service.py, línea 306, cambiar:          │
  │   embedding = self.modelo.encode(chunk).tolist()                │
  │ por:                                                             │
  │   embedding = self.modelo.encode(chunk)   # ya es ndarray       │
  └─────────────────────────────────────────────────────────────────┘
""")
elif INSERT_LIST_OK and INSERT_NUMPY_OK:
    print("""
  El INSERT funciona con ambos formatos en esta versión de pgvector.
  El problema de indexación NO está en el tipo de dato del embedding.
  Revisar: conexión, permisos, o la lógica de paper_ya_indexado().
""")
elif not INSERT_LIST_OK and not INSERT_NUMPY_OK:
    print("""
  AMBOS formatos de INSERT fallaron.
  El problema es de conexión o permisos en Supabase, no de formato.
""")

conn.close()
print("\n  Conexión cerrada.")
print("\n" + "="*70 + "\n")

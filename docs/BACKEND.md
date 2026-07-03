# Backend

FastAPI (Python 3.11) bajo `backend/app/`. Base URL: **`/api/v1`**. Swagger en `/docs`.

## Organización por capas

```
app/
  main.py                 # FastAPI app, CORS, lifespan (schemas Neo4j/Postgres), routers
  core/
    config.py             # Settings (pydantic-settings, lee .env) — fuente única del modelo LLM
    logging.py            # structlog a stdout
  api/routes/
    ingesta.py            # carga PDF, pipeline fase 1, SSE, PDF, Zotero, confirmación de revisión
    grafo.py              # lecturas del grafo + CRUD de revisión + ubicaciones + papers por referencia
    recuperacion.py       # motor de recuperación híbrida (estado, consultas, evidencia)
    auditoria.py          # auditoría LLM, veredictos, alertas, RAGAS, exportación Excel
    evaluacion.py         # evaluación vs experto (Kappa/F1) — separada de RAGAS
  models/                 # Pydantic por dominio: ingesta, grafo, auditoria, evaluacion
  services/
    ingesta/              # pdf_service, progreso_repository, localizacion_service
    grafo/                # extraccion_service, grafo_carga_service, neo4j_service
    externo/              # crossref, unpaywall, verificacion, zotero, embedding, doi_utils
    evaluacion/           # ragas_service, metricas_service, ground_truth_loader (scaffolding)
    llm/                  # openai_client, groq_client (retry sin temperature)
    storage/              # supabase_storage_service (bucket + cache local temporal)
    vectorstore/          # supabase_service (pgvector)
```

## Servicios principales

### Ingesta

- **`pdf_service`** — valida el PDF (extensión, tamaño `MAX_PDF_SIZE_MB`, firma `%PDF`), extrae Markdown con `pymupdf4llm` (con cache en disco junto al PDF) y detecta secciones. **Sanea caracteres invisibles** (U+200B/200C/200D/FEFF/00AD) en un único punto: sin esto, los encabezados tipo `**7.​** **Referencias bibliográficas**` no matchean los regex de sección.
- **`progreso_repository`** — persiste `ProgresoAuditoriaResponse` en la tabla `auditoria_progreso` (Supabase Postgres). Es lo que consulta el SSE.
- **`localizacion_service`** — abre el PDF con PyMuPDF y busca cada cita con `page.search_for` (fallback a variantes "Apellido (año)", "Apellido, año", "(Apellido, año)"). Devuelve página real 1-based + rectángulos con dimensiones de página, y la **página de la sección de referencias** (última página que contiene el encabezado — la primera suele ser el índice). Cachea en Storage `ubicaciones/{id}.json`; el CRUD de citas invalida el cache. `sincronizar_paginas` corrige `Cita.pagina` en Neo4j al final del pipeline.

### Grafo

- **`extraccion_service`** — extrae referencias APA (solo de la sección de referencias, truncando anexos) y citas APA (regex candidatas + LLM por bloques de ~7k chars con contexto previo). Deduplica citas por **contención de fragmentos**: mismo `texto_cita` con fragmentos contenidos uno en otro = misma ocurrencia (se conserva el más corto); la misma cita en párrafos distintos se conserva.
- **`grafo_carga_service`** — MERGE de documento/referencias/citas/autores en Neo4j, vinculación cita→referencia (apellido+año exacto 0.95 / parcial 0.75; la estrategia "solo año" fue eliminada por falsos positivos) y todo el CRUD de la revisión humana.
- **`neo4j_service`** — driver, schema (constraints) y conteos.

### Externo (verificación y fuentes)

- **`verificacion_service`** — verificación **solo por DOI**: CrossRef `GET /works/{doi}` (título oficial + abstract, sin búsqueda difusa) → Unpaywall (PDF open access → texto completo) → indexación en pgvector. Sin DOI → `no_encontrado`.
- **`zotero_service`** — importa `.ris` o `.zip` de Zotero. Matching: DOI normalizado exacto (confianza 1.0) → título normalizado ≥0.90 + primer autor + año (0.9). Sobrescribe metadatos del grafo con los de Zotero, asocia PDFs (adjuntos del ZIP vía campo L1, o descarga Unpaywall por DOI), limpia chunks previos y persiste el resumen en `zotero/{id}.json`.
- **`crossref_service` / `unpaywall_service`** — clientes HTTP con reintentos. `doi_utils` normaliza DOIs (`https://doi.org/…` → forma canónica) y genera la clave para `papers_chunks`.
- **`embedding_service`** — limpieza del texto del paper (trunca referencias, descarta chunks basura/portada), chunking, embeddings OpenAI y upsert en pgvector.

**Niveles de confianza de una referencia** (`nivel_confianza`):

| Valor | Significado |
|---|---|
| `texto_completo` | PDF descargado de Unpaywall e indexado |
| `zotero` | PDF adjunto del ZIP de Zotero indexado |
| `manual` | PDF subido a mano e indexado |
| `cache` | El paper ya estaba indexado de una corrida anterior |
| `abstract` | Solo se obtuvo el abstract de CrossRef |
| `sin_texto` | DOI válido pero sin texto accesible |
| `no_encontrado` | Sin DOI o sin fuente asociada |

### Auditoría y recuperación

- **`recuperacion_service`** — recuperación híbrida por cita: contexto del grafo (referencia vinculada) + búsqueda vectorial en los chunks del paper correspondiente.
- **`auditoria_service`** — por cita: construye el claim (aseveración del tesista), recupera evidencia y pide al LLM un veredicto `SUPPORTS | REFUTES | NO_INFO` con justificación; persiste todo en el nodo `Cita`.
- Alertas: citas sin referencia, referencias sin citar y `NO_INFO` como posibles alucinaciones.
- Exportación Excel (`openpyxl`): hoja Resumen + hoja Citas coloreada por veredicto. Las columnas RAGAS **solo** aparecen con `?incluir_ragas=true` (vista de administración).

### Evaluación (dos módulos separados — regla dura)

- **`ragas_service`** — calidad interna sin ground truth: `faithfulness`, `answer_relevancy`, `context_utilization` (expuesta como `context_precision`). Usa `settings.openai_model`.
- **`metricas_service`** — desempeño vs experto: matriz de confusión 3×3 (filas = experto, columnas = sistema, orden fijo `SUPPORTS, REFUTES, NO_INFO`), precisión/recall/F1 por categoría + macro + weighted (`zero_division=0`) y Kappa de Cohen (NaN → 0.0). Los pares no emparejados se cuentan aparte, sin romper. Resultado persistido en `evaluacion/{id}.json`.
- **`ground_truth_loader`** — solo scaffolding (`NotImplementedError`): documenta las opciones A(CSV)/B(Excel)/C(JSON)/D(UI) para cargar etiquetas del experto. Hoy las etiquetas viajan en el body de `POST /evaluacion/{id}/evaluar`.

### Infraestructura

- **`supabase_storage_service`** — bucket privado; mantiene un cache local determinista bajo `tmp` porque PyMuPDF exige rutas de archivo. La fuente de verdad es el bucket.
- **`supabase_service`** (vectorstore) — `papers_chunks` con `vector(1536)` e índice ivfflat coseno; upsert idempotente por id `{doi_norm}_{chunk_index}`.
- **`openai_client` / `groq_client`** — reintentos con backoff; si el modelo rechaza `temperature`, se deja de enviar (D1).

## Convenciones

- **Errores**: todo error de negocio responde `detail = {codigo, mensaje, accion_sugerida}` (`ErrorResponse`). El frontend muestra `accion_sugerida` tal cual.
- **Tareas largas**: extracción, verificación e importación Zotero corren en `threading.Thread(daemon=True)` y reportan avance a `auditoria_progreso`; el cliente sigue el SSE. Un fallo de importación **restaura el estado previo** del documento.
- **Modelo LLM**: nunca hardcodeado; siempre `settings.openai_model` / `settings.groq_model`.
- **Un documento a la vez**: no hay endpoints de lote.

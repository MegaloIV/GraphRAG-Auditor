# Backend — Documentación técnica

**GraphRAG Auditor API** · FastAPI · Python 3.11 · v0.1.0

Sistema de auditoría semántica de citas y referencias bibliográficas APA 7ma edición. Verifica que cada cita en una tesis sea fiel al documento original citado, usando un grafo de conocimiento (Neo4j) y búsqueda vectorial (Supabase pgvector).

---

## Índice

1. [Arquitectura general](#1-arquitectura-general)
2. [Configuración y variables de entorno](#2-configuración-y-variables-de-entorno)
3. [Arranque del servidor](#3-arranque-del-servidor)
4. [Módulo `core`](#4-módulo-core)
5. [Modelos Pydantic](#5-modelos-pydantic)
6. [Rutas API](#6-rutas-api)
7. [Servicios](#7-servicios)
8. [Base de datos y almacenamiento](#8-base-de-datos-y-almacenamiento)
9. [Pipeline de procesamiento](#9-pipeline-de-procesamiento)
10. [Logging](#10-logging)

---

## 1. Arquitectura general

```
PDF → [Ingesta] → [Extracción LLM] → [Grafo Neo4j] → [Verificación Externa]
                                                             ↓
                                             CrossRef + Unpaywall + Supabase
                                                             ↓
                                              [Motor de Recuperación Híbrida]
                                                             ↓
                                              [Auditoría Semántica GPT-4o-mini]
                                                             ↓
                                                    Veredictos + RAGAS
```

### Componentes externos

| Componente | Rol |
|---|---|
| **Neo4j AuraDB** | Grafo de conocimiento (Documentos, Referencias, Citas, Autores) |
| **Supabase pgvector** | Vector store para chunks de papers (embeddings 384D) |
| **OpenAI GPT-4o-mini** | Extracción de entidades y emisión de veredictos |
| **Groq Llama 3.3 70B** | LLM alternativo (configurable) |
| **CrossRef API** | Verificación de existencia de referencias académicas |
| **Unpaywall API** | Obtención de PDFs open access |

### Estructura de directorios

```
backend/
├── app/
│   ├── main.py                    # Punto de entrada FastAPI
│   ├── core/
│   │   ├── config.py              # Settings con pydantic-settings
│   │   └── logging.py             # Configuración structlog
│   ├── api/routes/
│   │   ├── ingesta.py             # Carga de PDF y pipeline
│   │   ├── grafo.py               # Consultas al grafo
│   │   ├── recuperacion.py        # Motor de búsqueda híbrida
│   │   └── auditoria.py           # Auditoría semántica y RAGAS
│   ├── models/
│   │   ├── ingesta.py             # Schemas de carga y progreso
│   │   ├── grafo.py               # Schemas de referencias y citas
│   │   └── auditoria.py           # Schemas de veredictos y alertas
│   └── services/
│       ├── ingesta/pdf_service.py
│       ├── grafo/
│       │   ├── extraccion_service.py
│       │   ├── grafo_carga_service.py
│       │   └── neo4j_service.py
│       ├── externo/
│       │   ├── crossref_service.py
│       │   ├── unpaywall_service.py
│       │   ├── embedding_service.py
│       │   └── verificacion_service.py
│       ├── recuperacion/recuperacion_service.py
│       ├── auditoria/auditoria_service.py
│       ├── evaluacion/ragas_service.py
│       ├── llm/
│       │   ├── openai_client.py
│       │   └── groq_client.py
│       └── vectorstore/supabase_service.py
└── data/
    ├── uploads/       # PDFs subidos
    ├── processed/     # Texto extraído y papers
    └── progreso/      # Estado SSE por documento (JSON)
```

---

## 2. Configuración y variables de entorno

Archivo: `backend/app/core/config.py`

Se carga desde `.env` con `pydantic-settings`. La instancia es un singleton cacheado con `@lru_cache`.

| Variable | Tipo | Defecto | Descripción |
|---|---|---|---|
| `APP_ENV` | str | `development` | Entorno de ejecución |
| `APP_PORT` | int | `8000` | Puerto del servidor |
| `GROQ_API_KEY` | str | `""` | API key de Groq |
| `GROQ_MODEL` | str | `llama-3.3-70b-versatile` | Modelo Groq |
| `OPENAI_API_KEY` | str | `""` | API key de OpenAI |
| `OPENAI_MODEL` | str | `gpt-4o-mini` | Modelo OpenAI |
| `CROSSREF_EMAIL` | str | — | Email para CrossRef API (obligatorio) |
| `NEO4J_URI` | str | `""` | URI de Neo4j AuraDB |
| `NEO4J_USERNAME` | str | `neo4j` | Usuario Neo4j |
| `NEO4J_PASSWORD` | str | `""` | Contraseña Neo4j |
| `NEO4J_DATABASE` | str | `neo4j` | Base de datos Neo4j |
| `SUPABASE_DB_HOST` | str | `""` | Host PostgreSQL de Supabase |
| `SUPABASE_DB_PORT` | int | `5432` | Puerto PostgreSQL |
| `SUPABASE_DB_NAME` | str | `postgres` | Nombre de la base de datos |
| `SUPABASE_DB_USER` | str | `postgres` | Usuario PostgreSQL |
| `SUPABASE_DB_PASSWORD` | str | `""` | Contraseña PostgreSQL |
| `MAX_PDF_SIZE_MB` | int | `10` | Tamaño máximo del PDF en MB |
| `UPLOAD_DIR` | str | `./data/uploads` | Carpeta de uploads |
| `PROCESSED_DIR` | str | `./data/processed` | Carpeta de procesados |
| `LOG_LEVEL` | str | `INFO` | Nivel de logging |

**Propiedades calculadas:**
- `max_pdf_size_bytes`: `max_pdf_size_mb * 1024 * 1024`
- `is_production`: `app_env == "production"`

---

## 3. Arranque del servidor

Archivo: `backend/app/main.py`

```python
uvicorn app.main:app --reload --port 8000
```

### Lifespan

Al arrancar:
1. Crea carpetas `upload_dir`, `processed_dir` y `./logs` si no existen.
2. Si Neo4j está configurado, inicializa el schema (constraints e índices).

Al apagar:
1. Cierra la conexión con Neo4j.

### CORS

Orígenes permitidos: `http://localhost:5173`, `http://localhost:3000`.

### Endpoints de salud

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Info de la API |
| `GET` | `/health` | Health check → `{"status": "ok"}` |

---

## 4. Módulo `core`

### `config.py`

Clase `Settings(BaseSettings)` — ver tabla de variables de entorno.

Función `get_settings() -> Settings` — singleton con `@lru_cache`.

### `logging.py`

Configura **structlog** con dos handlers:
- **Consola** (`stdout`): formato `ConsoleRenderer` sin colores.
- **Archivo** (`logs/app.log`): mismo formato, UTF-8.

Silencia logs verbosos de `uvicorn.access` y `httpx` (nivel WARNING).

Función `get_logger(name: str)` — retorna un logger structlog vinculado al módulo.

---

## 5. Modelos Pydantic

### `models/ingesta.py`

| Clase | Descripción |
|---|---|
| `EstadoIngesta` | Enum: `pendiente`, `procesando`, `listo_extraccion`, `verificando`, `completado`, `error` |
| `TipoSeccion` | Enum de secciones académicas: `titulo`, `resumen`, `introduccion`, `cuerpo`, `metodologia`, `resultados`, `discusion`, `conclusion`, `referencias`, `desconocido` |
| `DocumentoCargadoResponse` | Respuesta al subir un PDF: `documento_id`, `nombre_archivo`, `tamano_bytes`, `paginas`, `estado`, `mensaje` |
| `SeccionDetectada` | Una sección del documento: `tipo`, `titulo_detectado`, `pagina_inicio`, `pagina_fin`, `tiene_referencias` |
| `EstructuraDocumentoResponse` | Estructura completa del documento detectada |
| `ProgresoAuditoriaResponse` | Estado SSE: `documento_id`, `estado`, `porcentaje` (0–100), `mensaje_progreso`, `citas_encontradas?`, `error?` |
| `VerificacionSolicitud` | Body de verificación: `referencia_ids: list[str]` |
| `ErrorResponse` | Formato estándar de error: `codigo`, `mensaje`, `accion_sugerida` |

### `models/grafo.py`

| Clase | Descripción |
|---|---|
| `TipoCita` | Enum: `parentetica` (Autor, año), `narrativa` (Autor (año)) |
| `ReferenciaAPA` | Una referencia bibliográfica: `referencia_id`, `autores`, `anio?`, `titulo`, `fuente?`, `doi?`, `datos_incompletos`, `campos_faltantes`, `nivel_confianza?` |
| `ReferenciasResponse` | Lista de referencias del documento con totales e incompletas |
| `CitaEnTexto` | Una cita detectada: `cita_id`, `texto_cita`, `tipo`, `pagina`, `fragmento_oracion`, `referencia_id?` |
| `CitasResponse` | Lista de citas con conteos por tipo |
| `ResumenGrafo` | Estadísticas del grafo: nodos por tipo, relaciones, densidad, `grafo_robusto` (densidad ≥ 3.0) |

### `models/auditoria.py`

| Clase | Descripción |
|---|---|
| `VeredictoTipo` | Enum: `SUPPORTS`, `REFUTES`, `NO_INFO` |
| `VeredictoAuditoria` | Resultado de auditar una cita: veredicto, justificación, fragmento evidencia, similitud, métricas RAGAS opcionales |
| `MetricasRagasResponse` | Promedios RAGAS del documento: `faithfulness`, `answer_relevancy`, `context_precision` |
| `AuditoriaResponse` | Resumen completo de auditoría: totales por veredicto + lista de veredictos |
| `AlertaInconsistencia` | Una inconsistencia estructural: tipo, descripción, elemento, ubicación |
| `AlertasResponse` | Citas sin referencia + referencias sin citar |
| `AlertaAlucinacionSistema` | Cita con `NO_INFO`: no se pudo verificar |
| `AlertasAlucinacionResponse` | Lista de citas no verificables |

---

## 6. Rutas API

Base URL: `/api/v1`

### Ingesta (`/api/v1/ingesta`)

| Método | Ruta | HU | Descripción |
|---|---|---|---|
| `POST` | `/cargar` | HU-001 | Subir PDF. Arranca pipeline fase 1 en hilo daemon. Retorna `documento_id`. |
| `GET` | `/{doc_id}/estructura` | HU-002 | Estructura del documento (secciones detectadas). |
| `GET` | `/{doc_id}/progreso` | HU-003 | **SSE** — stream de progreso en tiempo real. Emite `ProgresoAuditoriaResponse` como JSON. Keep-alive cada 15 segundos. |
| `POST` | `/{doc_id}/verificar` | HU-VER | Inicia verificación externa (CrossRef + Unpaywall) de las referencias seleccionadas. Requiere estado `listo_extraccion` o `completado`. |

**Progreso en disco:** se persiste en `./data/progreso/{doc_id}.json` para que el SSE lo lea sin estado en memoria.

**Pipeline fase 1** (`_ejecutar_pipeline`):

```
10% → Extraer texto del PDF
25% → Detectar secciones
40% → Extraer referencias (LLM)
55% → Detectar citas (LLM)
70% → Cargar grafo (Neo4j)
90% → Vincular citas ↔ referencias
100% → Estado: listo_extraccion
```

**Pipeline fase 2** (`_ejecutar_verificacion`):

```
10% → Cargar referencias seleccionadas de Neo4j
30% → CrossRef + Unpaywall + Supabase (embedding)
100% → Estado: completado
```

---

### Grafo (`/api/v1/grafo`)

| Método | Ruta | HU | Descripción |
|---|---|---|---|
| `GET` | `/{doc_id}/referencias` | HU-004 | Lista de referencias desde Neo4j. |
| `GET` | `/{doc_id}/citas` | HU-005 | Lista de citas desde Neo4j. |
| `GET` | `/{doc_id}/resumen` | HU-006 | Resumen del grafo (nodos, relaciones, densidad). |
| `GET` | `/{doc_id}/grafo-visual` | — | Nodos y enlaces para visualización: `{nodes[], links[]}`. Incluye `nivel_confianza` en referencias. |
| `POST` | `/{doc_id}/referencias/{ref_id}/paper` | HU-VER | Subir PDF manualmente para una referencia no encontrada. Extrae texto, genera embedding e indexa en Supabase. |

---

### Recuperación (`/api/v1/recuperacion`)

| Método | Ruta | HU | Descripción |
|---|---|---|---|
| `GET` | `/{doc_id}/motor/estado` | HU-007 | Estado del motor: chunks indexados en Supabase y citas vinculadas en Neo4j. |
| `POST` | `/{doc_id}/motor/consultar` | HU-008 | Consulta por texto libre. Body: `{texto, n_resultados}`. |
| `GET` | `/{doc_id}/motor/cita/{cita_id}` | HU-008/009 | Evidencia completa para una cita: fragmento del paper + nodos del grafo. |
| `GET` | `/{doc_id}/motor/evidencia/{cita_id}` | HU-009 | Alias semántico del anterior, orientado a mostrar la ruta del grafo. |

**Schemas de respuesta:**
- `EstadoMotorSchema`: `listo`, `total_chunks`, `total_citas_vinculadas`, `total_referencias`, `mensaje`
- `ResultadoRecuperacionSchema`: `cita_id`, `texto_cita`, `fragmento_relevante`, `similitud`, `nodos_grafo[]`, `doi_referencia?`, `referencia_id?`, `metodo`
- `NodoGrafoSchema`: `tipo`, `nombre`, `relacion`, `propiedades`

---

### Auditoría (`/api/v1/auditoria`)

| Método | Ruta | HU | Descripción |
|---|---|---|---|
| `POST` | `/{doc_id}/auditar` | HU-010 | Audita todas las citas del documento. Paralelo (5 workers). Persiste veredictos en Neo4j. |
| `GET` | `/{doc_id}/veredictos` | HU-010 | Lee veredictos ya calculados desde Neo4j (sin llamar al LLM). |
| `GET` | `/{doc_id}/alertas` | HU-011 | Inconsistencias estructurales: citas sin referencia y referencias sin citar. |
| `GET` | `/{doc_id}/alertas/alucinaciones` | HU-012 | Citas con veredicto `NO_INFO` que el sistema no pudo verificar. |
| `POST` | `/{doc_id}/evaluar-ragas` | EP-RAGAS | Evalúa citas con fragmento de paper disponible usando RAGAS. Persiste scores. |
| `GET` | `/{doc_id}/metricas` | EP-RAGAS | Promedios RAGAS ya calculados del documento. |
| `GET` | `/{doc_id}/metricas/exportar` | — | Descarga Excel con dos hojas: **Resumen** (estadísticas) y **Citas** (detalle por cita). |

---

## 7. Servicios

### `services/ingesta/pdf_service.py`

- **`validar_pdf(contenido, filename)`**: valida magic bytes `%PDF`, tamaño máximo y extensión.
- **`extraer_texto(ruta_pdf) → (texto_md, num_paginas)`**: usa `pymupdf4llm.to_markdown()`.
- **`detectar_secciones(texto_md, num_paginas, doc_id) → EstructuraDocumentoResponse`**: identifica secciones académicas por regex sobre el texto Markdown.

**Excepción:** `PDFNoProcessableError(codigo, mensaje, accion)`.

---

### `services/grafo/extraccion_service.py`

Servicio de extracción de entidades con LLM.

**`extraer_referencias(texto) → list[ReferenciaAPA]`**

1. Trunca sección de anexos (`_truncar_antes_de_anexos`).
2. Localiza el inicio de la sección de referencias con regex.
3. Divide el texto en bloques de máx. 7 000 chars.
4. Para cada bloque: llama al LLM con `SYSTEM_REFERENCIAS` y parsea JSON (una ref por línea o array).

**`extraer_citas(texto, num_paginas) → list[CitaEnTexto]`**

1. Excluye la sección de referencias.
2. Detección previa por regex (`_detectar_citas_regex`) para 5 patrones APA.
3. Para cada bloque: llama al LLM con `SYSTEM_CITAS` y contexto del bloque anterior.
4. Valida que `fragmento_oracion ≠ texto_cita`; usa fallback de extracción por posición.
5. Trunca fragmentos parentéticos al cierre `)` de la cita.
6. Deduplica por `(texto_cita, fragmento_oracion[:80])`.

**Helpers internos:**
- `_encontrar_inicio_referencias(texto)`: regex en dos pasadas (encabezado markdown y numerado).
- `_truncar_antes_de_anexos(texto)`: descarta desde el encabezado de Anexos.
- `_dividir_en_bloques(texto, max_chars=7000)`: agrupa párrafos; rompe párrafos gigantes por oraciones.
- `_parsear_json(texto)`: tolerante a JSON malformado (intenta array, líneas NDJSON y recuperación parcial).

---

### `services/grafo/grafo_carga_service.py`

Carga datos al grafo Neo4j.

| Método | Descripción |
|---|---|
| `cargar_documento(doc_id, nombre)` | `MERGE` del nodo `Documento`. |
| `cargar_referencias(doc_id, refs)` | `MERGE` de nodos `Referencia` + `Autor`, relaciones `TIENE_REFERENCIA` y `ESCRITO_POR`. |
| `cargar_citas(doc_id, citas)` | `MERGE` de nodos `Cita`, relación `TIENE_CITA`. |
| `vincular_citas(doc_id)` | Vincula `Cita → CITA_A → Referencia` por apellido + año (3 estrategias). |
| `obtener_resumen_grafo(doc_id)` | Estadísticas del grafo para el documento. |

**Estrategias de vinculación `CITA_A`:**
1. Apellido exacto + año → confianza 0.95
2. Apellido parcial + año (inicio coincide) → confianza 0.75

La estrategia 3 (solo año) fue eliminada por generar falsos positivos.

---

### `services/grafo/neo4j_service.py`

Singleton `Neo4jService` con reconexión automática.

**Schema al arrancar** (via `inicializar_schema()`):

Constraints únicos sobre: `Documento.id`, `Referencia.id`, `Cita.id`, `Autor.nombre_normalizado`.

Índices sobre: `Autor.nombre`, `Referencia.titulo`, `Referencia.anio`, `Cita.texto`.

**`contar_nodos_y_relaciones(doc_id)`**: retorna `referencias`, `citas`, `autores`, `relaciones` totales y `citas_vinculadas`.

---

### `services/externo/crossref_service.py`

Busca referencias en CrossRef API (gratuita, >130M papers).

**`buscar_referencia(titulo, autores, anio?) → ResultadoCrossRef`**

1. Paso 1: query con título + primer autor, con filtro de año.
2. Paso 2 (fallback): solo título, sin filtro de año.
3. Usa `_calcular_score()` (Jaccard de palabras). Descarta resultados con score < 0.30.
4. Reintentos exponenciales en timeout.

`ResultadoCrossRef`: `encontrado`, `doi?`, `titulo_oficial?`, `autores_oficiales`, `anio_oficial?`, `abstract?`, `url_pdf?`, `score_coincidencia`.

---

### `services/externo/unpaywall_service.py`

Busca PDFs open access vía Unpaywall API.

**`buscar_pdf_gratuito(doi) → ResultadoUnpaywall`**

1. Consulta `api.unpaywall.org/v2/{doi}`.
2. Si es OA, extrae la mejor URL de PDF (publisher > repositorio > preprint).
3. Descarga el PDF, extrae texto con `pymupdf4llm`.
4. Cachea el texto extraído en `data/processed/papers/{doi_normalizado}.txt`.
5. Elimina el PDF descargado para ahorrar espacio.

---

### `services/externo/embedding_service.py`

Modelo local: `all-MiniLM-L6-v2` (384 dimensiones, carga lazy).

**`indexar_paper(doi, texto, metadata) → bool`**

Pipeline de limpieza e indexación:
1. `_truncar_referencias_paper()`: elimina la bibliografía del paper antes de indexar (regex + patrón Vancouver).
2. `_dividir_en_chunks()`: por form-feed `\f`, marcadores textuales de página o por tamaño (máx. 2 500 chars).
3. Filtra chunks basura (`_es_chunk_basura`) y portadas (`_es_chunk_portada`).
4. Borra chunks previos del DOI en Supabase.
5. Genera embeddings y guarda en Supabase.

**`buscar_similares(texto_consulta, n_resultados, filtro_doi?) → list[dict]`**

Busca en Supabase por similitud coseno. Retorna `[{texto, metadata:{pagina, chunk_index, doi}, similitud}]`.

---

### `services/externo/verificacion_service.py`

Orquestador de verificación externa.

**`verificar_referencias(referencias, doc_id, max_referencias)`**

Por cada referencia:
1. CrossRef → DOI + abstract.
2. Si hay DOI y no está en caché: Unpaywall → PDF completo.
3. Indexar texto disponible en Supabase con `embedding_service.indexar_paper()`.
4. Actualizar nodo `Referencia` en Neo4j con `doi_verificado`, `nivel_confianza` y `titulo_oficial`.

`nivel_confianza` posibles: `"texto_completo"`, `"abstract"`, `"cache"`, `"no_encontrado"`.

---

### `services/recuperacion/recuperacion_service.py`

Motor de recuperación híbrida (GraphRAG).

**`consultar_cita(doc_id, cita_id, fragmento_oracion?) → ResultadoRecuperacion`**

1. Traversal de grafo (Neo4j): `Cita → CITA_A → Referencia → ESCRITO_POR → Autor`.
2. Búsqueda vectorial en Supabase filtrando por `doi_normalizado` de la referencia.
3. Concatena los 3 mejores fragmentos (máx. 3 000 chars).
4. `metodo`: `"hibrido"` (grafo + vectorial), `"solo_grafo"` (sin fragmento), `"no_encontrado"`.

**`consultar_texto_libre(doc_id, texto_consulta, n_resultados) → list[ResultadoRecuperacion]`**

Búsqueda vectorial global (sin filtro de DOI) cruzada con las citas del documento por DOI.

**`estado_motor(doc_id) → EstadoMotor`**

`listo = total_chunks > 0 AND citas_vinculadas > 0`.

---

### `services/auditoria/auditoria_service.py`

Auditoría semántica con GPT-4o-mini.

**`auditar_documento(doc_id) → list[VeredictoAuditoria]`**

Ejecuta auditoría en paralelo (5 workers con `ThreadPoolExecutor`) sobre todas las citas.

Por cita (`_auditar_cita`):
1. Si no tiene referencia vinculada → `NO_INFO`.
2. Si el paper no está indexado en Supabase → `NO_INFO`.
3. `recuperacion_service.consultar_cita()` → fragmento.
4. Si no hay fragmento → `NO_INFO`.
5. `_llamar_llm()` → prompt con afirmación + cita APA + fragmento del paper.
6. `_parsear_respuesta_llm()` → extrae `VERDICT:` y `JUSTIFICATION:`.
7. Persiste veredicto en Neo4j (`_persistir_veredicto`).

**System prompt (`SYSTEM_AUDITORIA`):** Auditor académico APA 7ma. Evalúa semántica (no idioma). Formato de respuesta: `VERDICT: <SUPPORTS|REFUTES|NO_INFO>` + `JUSTIFICATION: <oración>`.

**`detectar_inconsistencias(doc_id)`**: citas sin `CITA_A` + referencias sin ningún `CITA_A` apuntando a ellas.

**`evaluar_ragas_documento(doc_id)`**: evalúa con RAGAS las citas que tienen `fragmento_evidencia` no vacío. Persiste `faithfulness`, `answer_relevancy`, `context_precision` en cada nodo `Cita`.

---

### `services/evaluacion/ragas_service.py`

Evaluación con framework RAGAS (sin ground truth externo).

**Métricas evaluadas:**
- `faithfulness`: ¿La respuesta del sistema está anclada en el fragmento recuperado?
- `answer_relevancy`: ¿El veredicto es relevante al claim verificado?
- `context_precision`: ¿El fragmento recuperado es pertinente para el claim?

LLM: `gpt-4o-mini` vía `langchain_openai`. Embeddings: `text-embedding-3-small`.

---

### `services/llm/openai_client.py` y `groq_client.py`

Clientes LLM con interfaz `completar(system_prompt, user_prompt) → str`. El sistema usa OpenAI por defecto para extracción y auditoría.

---

### `services/vectorstore/supabase_service.py`

Interfaz con Supabase pgvector (PostgreSQL).

Operaciones clave: `indexar_chunks(registros)`, `buscar_similares(embedding, doi_normalizado?, top_k)`, `eliminar_chunks_por_doi(doi_normalizado)`, `paper_ya_indexado(doi_normalizado)`, `total_chunks()`.

---

## 8. Base de datos y almacenamiento

### Grafo Neo4j

**Nodos:**

| Label | Propiedades clave |
|---|---|
| `Documento` | `id`, `nombre_archivo`, `actualizado_en` |
| `Referencia` | `id`, `titulo`, `anio`, `fuente`, `doi`, `doi_verificado`, `titulo_oficial`, `nivel_confianza`, `score_crossref`, `datos_incompletos`, `verificado`, `verificado_en` |
| `Cita` | `id`, `texto`, `tipo`, `pagina`, `fragmento`, `veredicto`, `justificacion`, `fragmento_evidencia`, `pagina_paper`, `auditado_en`, `faithfulness`, `answer_relevancy`, `context_precision`, `ragas_evaluado_en` |
| `Autor` | `nombre_normalizado` (unique), `nombre` |

**Relaciones:**

| Relación | Origen → Destino |
|---|---|
| `TIENE_REFERENCIA` | `Documento → Referencia` |
| `TIENE_CITA` | `Documento → Cita` |
| `ESCRITO_POR` | `Referencia → Autor` |
| `CITA_A` | `Cita → Referencia` · props: `confianza`, `metodo` |

### Supabase pgvector

Tabla de chunks de papers. Columnas principales: `id`, `doi`, `doi_normalizado`, `titulo`, `anio`, `pagina`, `chunk_index`, `nivel_confianza`, `referencia_id`, `contenido`, `embedding` (vector 384D).

### Sistema de archivos

```
data/
├── uploads/{doc_id}.pdf          # PDFs subidos
├── processed/papers/{doi}.txt    # Texto extraído de papers
└── progreso/{doc_id}.json        # Estado SSE del pipeline
```

---

## 9. Pipeline de procesamiento

### Fase 1 — Extracción (automática al subir)

```
PDF
 │
 ├─ pdf_service.extraer_texto()       → texto Markdown + num_páginas
 ├─ pdf_service.detectar_secciones()  → estructura del documento
 ├─ extraccion_service.extraer_referencias() → list[ReferenciaAPA]  (LLM)
 ├─ extraccion_service.extraer_citas()       → list[CitaEnTexto]     (LLM)
 ├─ grafo_carga_service.cargar_documento()
 ├─ grafo_carga_service.cargar_referencias()
 ├─ grafo_carga_service.cargar_citas()
 └─ grafo_carga_service.vincular_citas()  → CITA_A en Neo4j
```

Estado final: `listo_extraccion` — el usuario selecciona referencias a verificar.

### Fase 2 — Verificación (por selección del usuario)

```
referencias_seleccionadas
 │
 ├─ crossref_service.buscar_referencia()  → DOI + abstract
 ├─ unpaywall_service.buscar_pdf_gratuito() → texto completo (si OA)
 ├─ embedding_service.indexar_paper()     → chunks en Supabase
 └─ neo4j: UPDATE Referencia (doi_verificado, nivel_confianza)
```

Estado final: `completado`.

### Fase 3 — Auditoría (manual, por el usuario)

```
todas las citas del documento
 │ (paralelo, 5 workers)
 ├─ recuperacion_service.consultar_cita()  → fragmento del paper
 ├─ llm_service.completar(SYSTEM_AUDITORIA, ...) → veredicto
 └─ neo4j: SET Cita.veredicto, .justificacion, .fragmento_evidencia
```

### Fase 4 — RAGAS (opcional)

```
citas con fragmento_evidencia
 │ (secuencial)
 └─ ragas_service.evaluar_cita() → faithfulness, answer_relevancy, context_precision
    └─ neo4j: SET Cita.faithfulness, .answer_relevancy, .context_precision
```

---

## 10. Logging

Se usa **structlog** con logging estructurado. Los eventos se emiten con claves snake_case:

```python
logger.info("pdf_cargado", doc_id=documento_id, paginas=num_paginas)
logger.error("error_auditar_documento", doc_id=documento_id, error=str(e))
```

Salida en consola y en `logs/app.log` con timestamp ISO 8601.

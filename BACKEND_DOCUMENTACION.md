# Documentación Técnica — Backend: GraphRAG Auditor

> Generado el 2026-05-30 a partir del análisis completo de `backend/app/`

---

## Índice

1. [Propósito del sistema](#1-propósito-del-sistema)
2. [Arquitectura](#2-arquitectura)
3. [Tecnologías utilizadas](#3-tecnologías-utilizadas)
4. [Estructura de carpetas](#4-estructura-de-carpetas)
5. [Flujo completo de extremo a extremo](#5-flujo-completo-de-extremo-a-extremo)
6. [Módulo por módulo](#6-módulo-por-módulo)
   - [6.1 Punto de entrada — `main.py`](#61-punto-de-entrada--mainpy)
   - [6.2 Core — Configuración y logging](#62-core--configuración-y-logging)
   - [6.3 Modelos de datos — `models/`](#63-modelos-de-datos--models)
   - [6.4 API REST — `api/routes/`](#64-api-rest--apiroutes)
   - [6.5 Servicios de ingesta — `services/ingesta/`](#65-servicios-de-ingesta--servicesingesta)
   - [6.6 Servicios de grafo — `services/grafo/`](#66-servicios-de-grafo--servicesgrafo)
   - [6.7 Servicios externos — `services/externo/`](#67-servicios-externos--servicesexterno)
   - [6.8 Vector store — `services/vectorstore/`](#68-vector-store--servicesvectorstore)
   - [6.9 Clientes LLM — `services/llm/`](#69-clientes-llm--servicesllm)
   - [6.10 Motor de recuperación — `services/recuperacion/`](#610-motor-de-recuperación--servicesrecuperacion)
   - [6.11 Auditoría semántica — `services/auditoria/`](#611-auditoría-semántica--servicesauditoria)
   - [6.12 Evaluación RAGAS — `services/evaluacion/`](#612-evaluación-ragas--servicesevaluacion)
7. [Modelo de datos Neo4j (grafo)](#7-modelo-de-datos-neo4j-grafo)
8. [Modelo de datos Supabase (vector store)](#8-modelo-de-datos-supabase-vector-store)
9. [Tests](#9-tests)
10. [Metodología de desarrollo detectada](#10-metodología-de-desarrollo-detectada)
11. [Decisiones de diseño relevantes](#11-decisiones-de-diseño-relevantes)

---

## 1. Propósito del sistema

**GraphRAG Auditor** es un sistema de auditoría semántica de citas y referencias bibliográficas en norma **APA 7ma edición**. Recibe un documento académico en PDF (p.ej., una tesis), extrae todas las citas y referencias que contiene, las verifica contra bases de datos académicas externas, construye un grafo de conocimiento, y finalmente emite un **veredicto** por cada cita:

| Veredicto | Significado |
|---|---|
| `VÁLIDA` | La afirmación del tesista refleja fielmente lo que dice el paper citado |
| `DUDOSA` | Hay exageración, simplificación excesiva o distorsión parcial |
| `ALUCINADA` | La afirmación dice algo que el paper no dice o contradice directamente |
| `NO_VERIFICABLE` | El sistema no encontró evidencia suficiente para emitir un veredicto |

El sistema también detecta **inconsistencias estructurales**: citas sin referencia bibliográfica correspondiente y referencias listadas que nunca se citan en el cuerpo del texto.

---

## 2. Arquitectura

### Nombre de la arquitectura

El sistema implementa **GraphRAG** (*Graph Retrieval-Augmented Generation*), una variante avanzada de RAG que enriquece la recuperación de contexto con un **grafo de conocimiento** en lugar de depender únicamente de búsqueda vectorial. Dentro del backend, la organización del código sigue un patrón de **arquitectura en capas** (*Layered Architecture*) con fuerte influencia de **Domain-Driven Design (DDD)**:

```
┌─────────────────────────────────────────────────────────┐
│                   Capa de Presentación                  │
│          FastAPI — Routers (api/routes/)                │
├─────────────────────────────────────────────────────────┤
│                  Capa de Dominio / Modelos              │
│          Pydantic Schemas (models/)                     │
├─────────────────────────────────────────────────────────┤
│                  Capa de Servicios (lógica)             │
│  ingesta | grafo | externo | recuperacion | auditoria   │
│  vectorstore | llm | evaluacion                         │
├─────────────────────────────────────────────────────────┤
│                  Capa de Infraestructura                │
│    Neo4j (grafo) | Supabase/pgvector (vectores)        │
│    OpenAI API | CrossRef API | Unpaywall API            │
└─────────────────────────────────────────────────────────┘
```

### Diagrama conceptual del flujo GraphRAG

```
PDF del tesista
      │
      ▼
 [Ingesta + LLM]  ←── OpenAI GPT-4o-mini
      │  extrae citas y referencias
      ▼
  [Neo4j]  ←────── Knowledge Graph
  Nodos: Documento, Referencia, Autor, Cita
  Relaciones: TIENE_REFERENCIA, TIENE_CITA, ESCRITO_POR, CITA_A
      │
      │ CrossRef + Unpaywall
      ▼
 [Supabase pgvector]  ←── embeddings (all-MiniLM-L6-v2)
  Chunks del paper citado indexados con vectores semánticos
      │
      │ Por cada cita: traversal grafo + búsqueda vectorial
      ▼
  [Auditoría LLM]  ←── GPT-4o-mini
  Veredicto: VÁLIDA | DUDOSA | ALUCINADA | NO_VERIFICABLE
      │
      ▼
 [Evaluación RAGAS]  ←── métricas de calidad RAG
```

### Patrón Singleton en servicios

Todos los servicios se instancian como **singletons** al pie del archivo. Esto garantiza que la conexión a Neo4j, la conexión a Supabase y el modelo de embeddings se carguen **una sola vez** en toda la vida del proceso, evitando reconexiones innecesarias.

```python
# Ejemplo en neo4j_service.py
neo4j_service = Neo4jService()  # singleton global
```

---

## 3. Tecnologías utilizadas

### Framework y servidor

| Tecnología | Versión | Rol |
|---|---|---|
| **FastAPI** | 0.136.1 | Framework HTTP asíncrono (routers, validación, docs automáticas) |
| **Uvicorn** | 0.47.0 | Servidor ASGI de alto rendimiento |
| **Pydantic v2** | 2.13.4 | Validación y serialización de modelos de datos |
| **pydantic-settings** | 2.14.1 | Carga de configuración desde `.env` |
| **Starlette** | 1.0.1 | Base de FastAPI; también maneja SSE (Server-Sent Events) |

### Bases de datos

| Tecnología | Rol |
|---|---|
| **Neo4j AuraDB** | Base de datos de grafos. Almacena el Knowledge Graph: Documentos, Referencias, Autores, Citas y sus relaciones |
| **Supabase + pgvector** | PostgreSQL con extensión de vectores. Almacena los chunks de papers con sus embeddings para búsqueda semántica |
| **psycopg2-binary** | Driver Python para PostgreSQL / Supabase |
| **pgvector** | Extensión PostgreSQL + adaptador Python para operaciones ANN (cosine similarity) |

### Inteligencia Artificial

| Tecnología | Rol |
|---|---|
| **OpenAI GPT-4o-mini** | Modelo principal para: extracción de referencias, detección de citas y auditoría semántica de veredictos |
| **Groq + LLaMA-3.3-70b** | Cliente alternativo de LLM (disponible como backup, misma interfaz) |
| **sentence-transformers** | Genera embeddings locales con el modelo `all-MiniLM-L6-v2` (384 dimensiones, sin costo de API) |
| **RAGAS** | Framework de evaluación de sistemas RAG: mide faithfulness, answer relevancy, context precision/recall, answer correctness |
| **LangChain** | Usado como wrapper de RAGAS para conectar con OpenAI |

### Procesamiento de documentos

| Tecnología | Rol |
|---|---|
| **PyMuPDF (fitz)** | Extrae número de páginas y metadatos del PDF |
| **pymupdf4llm** | Convierte PDFs a Markdown estructurado, optimizado para LLMs |
| **pdfminer.six** | Alternativa de extracción (disponible como dependencia) |
| **pdfplumber** | Alternativa de extracción (disponible como dependencia) |

### APIs externas

| API | Rol | Costo |
|---|---|---|
| **CrossRef API** | Verifica existencia de referencias, obtiene DOI oficial y abstract | Gratuita |
| **Unpaywall API** | Busca versiones de acceso abierto de los papers (PDF gratuito legal) | Gratuita (requiere email) |

### Observabilidad

| Tecnología | Rol |
|---|---|
| **structlog** | Logging estructurado con salida JSON/texto + timestamp ISO |
| **openpyxl** | Generación de reportes Excel con scores RAGAS por cita |



## 4. Estructura de carpetas

```
backend/
├── app/
│   ├── main.py                    # Punto de entrada FastAPI + lifespan
│   ├── core/
│   │   ├── config.py              # Settings desde .env (pydantic-settings)
│   │   └── logging.py             # Configuración structlog
│   ├── models/
│   │   ├── ingesta.py             # Schemas de carga de PDF y progreso
│   │   ├── grafo.py               # Schemas de referencias, citas, grafo
│   │   └── auditoria.py           # Schemas de veredictos, alertas, RAGAS
│   ├── api/
│   │   └── routes/
│   │       ├── ingesta.py         # EP-001: /api/v1/ingesta/
│   │       ├── grafo.py           # EP-002: /api/v1/grafo/
│   │       ├── recuperacion.py    # EP-003: /api/v1/recuperacion/
│   │       └── auditoria.py       # EP-004: /api/v1/auditoria/
│   └── services/
│       ├── ingesta/
│       │   └── pdf_service.py     # Validación + extracción de texto PDF
│       ├── grafo/
│       │   ├── extraccion_service.py  # LLM: extrae referencias y citas
│       │   ├── grafo_carga_service.py # Carga el grafo en Neo4j
│       │   └── neo4j_service.py       # Conexión + schema Neo4j
│       ├── externo/
│       │   ├── crossref_service.py    # Búsqueda de papers en CrossRef
│       │   ├── unpaywall_service.py   # Descarga de PDFs open access
│       │   ├── embedding_service.py   # Chunking + embeddings + indexado
│       │   └── verificacion_service.py # Orquestador de verificación externa
│       ├── vectorstore/
│       │   └── supabase_service.py    # CRUD en Supabase pgvector
│       ├── llm/
│       │   ├── openai_client.py       # Cliente OpenAI con reintentos
│       │   └── groq_client.py         # Cliente Groq con reintentos
│       ├── recuperacion/
│       │   └── recuperacion_service.py # Motor híbrido grafo + vector
│       ├── auditoria/
│       │   └── auditoria_service.py   # Veredictos + inconsistencias + RAGAS
│       └── evaluacion/
│           └── ragas_service.py       # Wrapper RAGAS para métricas
├── tests/
│   ├── unit/                      # Tests sin conexión a servicios reales
│   │   ├── test_auditoria_service.py
│   │   ├── test_embedding_service.py
│   │   ├── test_extraccion_service.py
│   │   ├── test_pdf_service.py
│   │   └── test_recuperacion_service.py
│   ├── integration/               # Tests con servicios reales (vacío actualmente)
│   ├── diagnostico_chroma.py      # Script de diagnóstico vectorstore
│   ├── diagnostico_citas.py       # Script de diagnóstico extracción de citas
│   ├── diagnostico_ragas.py       # Script de diagnóstico evaluación RAGAS
│   └── test_embedding_pipeline.py # Test del pipeline completo de embeddings
├── data/
│   ├── uploads/                   # PDFs subidos (nombrados por UUID)
│   ├── progreso/                  # JSONs de progreso por documento
│   └── processed/
│       └── papers/                # Textos de papers descargados + cacheados
├── logs/
│   └── app.log                    # Log rotativo de la aplicación
├── requirements.txt
└── .env                           # Variables de entorno (no versionado)
```

---

## 5. Flujo completo de extremo a extremo

Este es el flujo que ocurre cuando un usuario sube un PDF a la plataforma. Los porcentajes corresponden al progreso reportado por SSE al frontend.

```
Usuario sube PDF
       │
       ▼
POST /api/v1/ingesta/cargar
  ├─ Validar: extensión .pdf, tamaño ≤ 10 MB, magic bytes %PDF
  ├─ Guardar en ./data/uploads/{uuid}.pdf
  ├─ Lanzar threading.Thread con _ejecutar_pipeline()
  └─ Responder inmediatamente → {documento_id, estado: "procesando"}

          ┌─────────── PIPELINE ASÍNCRONO (hilo separado) ───────────┐
          │                                                           │
          │  10%  PyMuPDF / pymupdf4llm                              │
          │       PDF → Markdown estructurado (caché en .md)         │
          │                                                           │
          │  25%  Detección de secciones                             │
          │       Regex sobre headings Markdown → TipoSeccion enum   │
          │       (Introducción, Metodología, Referencias, etc.)      │
          │                                                           │
          │  40%  Extracción de referencias [OpenAI GPT-4o-mini]     │
          │       Solo sección de Referencias                         │
          │       Texto dividido en bloques de 7000 chars            │
          │       LLM → JSON por línea → ReferenciaAPA[]             │
          │                                                           │
          │  55%  Extracción de citas [OpenAI GPT-4o-mini]           │
          │       Solo cuerpo del texto (excluye sección Referencias)│
          │       Regex pre-filtra candidatos → LLM confirma y       │
          │       extrae tipo (parentética/narrativa) + fragmento     │
          │       Deduplicación por (texto_cita, pagina)              │
          │                                                           │
          │  70%  Carga del grafo en Neo4j                           │
          │       MERGE Documento, Referencia, Autor, Cita           │
          │       Relaciones: TIENE_REFERENCIA, TIENE_CITA,          │
          │       ESCRITO_POR                                         │
          │                                                           │
          │  78%  Vinculación citas ↔ referencias                    │
          │       Algoritmo en cascada (apellido+año exacto → 0.95,  │
          │       apellido parcial+año → 0.75, solo año → 0.50)      │
          │       Crea relación (Cita)-[:CITA_A]->(Referencia)       │
          │                                                           │
          │  85%  Verificación externa (máx. 10 refs)                │
          │       Por cada referencia:                                │
          │         1. CrossRef API → DOI + abstract                 │
          │         2. Si hay DOI → Unpaywall → PDF open access      │
          │         3. pymupdf4llm → texto completo del paper        │
          │         4. Truncar referencias del paper, limpiar chunks │
          │         5. Modelo local all-MiniLM-L6-v2 → embeddings   │
          │         6. Supabase pgvector → almacenar chunks          │
          │         7. Neo4j → actualizar nodo Referencia con DOI    │
          │                                                           │
          │ 100%  Estado COMPLETADO guardado en JSON de progreso     │
          └───────────────────────────────────────────────────────────┘

El frontend conecta SSE: GET /api/v1/ingesta/{doc_id}/progreso
  └─ Lee el JSON de progreso del disco cada 500 ms
  └─ Envía event-stream hasta estado COMPLETADO o ERROR

─── FASE DE CONSULTA (usuario interactúa con resultados) ───────────

GET  /api/v1/grafo/{doc_id}/referencias     → Lee Referencias de Neo4j
GET  /api/v1/grafo/{doc_id}/citas           → Lee Citas de Neo4j
GET  /api/v1/grafo/{doc_id}/resumen         → Estadísticas del grafo
GET  /api/v1/recuperacion/{doc_id}/motor/estado → Chunks + vínculos listos?

─── FASE DE AUDITORÍA ──────────────────────────────────────────────

POST /api/v1/auditoria/{doc_id}/auditar
  └─ Por cada cita (paralelismo ThreadPoolExecutor, max 5 workers):
       1. Neo4j: Cita → CITA_A → Referencia → DOI verificado
       2. Si el paper NO está en Supabase → veredicto NO_VERIFICABLE
       3. Si está: generar embedding del texto de la cita
       4. Supabase cosine search → chunk más similar del paper
       5. GPT-4o-mini: [afirmación + cita + fragmento] → VÁLIDA/DUDOSA/ALUCINADA
       6. Neo4j: SET c.veredicto, c.justificacion en nodo Cita

GET  /api/v1/auditoria/{doc_id}/veredictos    → Lee veredictos de Neo4j
GET  /api/v1/auditoria/{doc_id}/alertas       → Citas sin ref + refs sin citar
GET  /api/v1/auditoria/{doc_id}/alertas/alucinaciones → NO_VERIFICABLE

─── FASE DE EVALUACIÓN RAGAS ───────────────────────────────────────

POST /api/v1/auditoria/{doc_id}/evaluar-ragas
  └─ Lee citas con fragmento_evidencia en Neo4j
  └─ Por cada cita: RAGAS evalúa con GPT-4o-mini + text-embedding-3-small
  └─ 5 métricas: faithfulness, answer_relevancy, context_precision,
                 context_recall, answer_correctness
  └─ Persiste scores en Neo4j (nodo Cita)

GET  /api/v1/auditoria/{doc_id}/metricas          → Promedios RAGAS
GET  /api/v1/auditoria/{doc_id}/metricas/exportar → Excel con scores por cita
```

---

## 6. Módulo por módulo

### 6.1 Punto de entrada — `main.py`

`main.py` es el archivo que instancia la aplicación FastAPI y la configura completa.

**Lifespan (arranque/apagado):** FastAPI usa el decorator `@asynccontextmanager` para ejecutar código al iniciar y al apagar el servidor. Al arrancar: crea carpetas necesarias (`./data/uploads`, `./data/processed`, `./logs`), e inicializa el **schema de Neo4j** (constraints e índices) si las credenciales están disponibles. Al apagar: cierra la conexión Neo4j limpiamente.

**CORS:** Permite solicitudes desde `localhost:5173` (Vite dev), `localhost:3000` y el dominio de producción en Vercel. Esto es necesario porque el frontend React corre en un origen diferente al backend.

**Routers:** Los cuatro routers se registran bajo el prefijo `/api/v1`, lo que da URLs como `/api/v1/ingesta/cargar`.

---

### 6.2 Core — Configuración y logging

#### `core/config.py`

Usa `pydantic-settings` para leer todas las variables de entorno del archivo `.env`. La clase `Settings` define los valores por defecto y tipos. La función `get_settings()` está decorada con `@lru_cache`, lo que garantiza que solo se crea **una instancia** de Settings en toda la vida del proceso (patrón singleton).

Variables clave:
- `groq_api_key`, `groq_model` — LLM alternativo
- `openai_api_key`, `openai_model` — LLM principal (GPT-4o-mini)
- `neo4j_uri`, `neo4j_username`, `neo4j_password` — grafo de conocimiento
- `supabase_db_*` — conexión PostgreSQL de Supabase
- `max_pdf_size_mb`, `upload_dir` — límites de ingesta

#### `core/logging.py`

Configura `structlog` para logging estructurado. Los logs se escriben en **dos destinos** simultáneamente: consola (`stdout`) y archivo (`./logs/app.log`). El formato incluye: nombre del logger, nivel, timestamp ISO, y los campos clave-valor que cada servicio agrega al log (p.ej. `doc_id=...`, `paginas=...`). Los loggers de `uvicorn.access` y `httpx` se silencian a nivel WARNING para reducir ruido.

---

### 6.3 Modelos de datos — `models/`

Los modelos son **Pydantic v2 BaseModel**, que actúan como contratos entre capas. Definen exactamente qué datos pueden entrar y salir por la API.

#### `models/ingesta.py`

| Clase | Propósito |
|---|---|
| `EstadoIngesta` (Enum) | `pendiente`, `procesando`, `completado`, `error` |
| `TipoSeccion` (Enum) | Tipos de sección: título, resumen, introducción, metodología, referencias, etc. |
| `DocumentoCargadoResponse` | Respuesta al subir un PDF: `documento_id`, `nombre_archivo`, `paginas`, `estado` |
| `SeccionDetectada` | Una sección del documento: tipo, título, página inicio/fin |
| `EstructuraDocumentoResponse` | Lista de secciones + bandera `tiene_seccion_referencias` |
| `ProgresoAuditoriaResponse` | Estado en tiempo real: porcentaje, mensaje, citas encontradas |

#### `models/grafo.py`

| Clase | Propósito |
|---|---|
| `TipoCita` (Enum) | `parentetica` (Autor, año) o `narrativa` (Autor (año)) |
| `ReferenciaAPA` | Una referencia bibliográfica: autores, año, título, fuente, DOI, campos faltantes |
| `CitaEnTexto` | Una cita detectada: texto, tipo, página, fragmento de oración |
| `ResumenGrafo` | Estadísticas del grafo: nodos, relaciones, densidad, si es robusto |

#### `models/auditoria.py`

| Clase | Propósito |
|---|---|
| `VeredictoTipo` (Enum) | `VÁLIDA`, `DUDOSA`, `ALUCINADA`, `NO_VERIFICABLE` |
| `VeredictoAuditoria` | Veredicto de una cita con todos sus metadatos y scores RAGAS |
| `AuditoriaResponse` | Resumen de la auditoría: totales por veredicto + lista de veredictos |
| `AlertaInconsistencia` | Una inconsistencia estructural: tipo, descripción, elemento, ubicación |
| `AlertasAlucinacionResponse` | Lista de citas NO_VERIFICABLE |
| `MetricasRagasResponse` | Promedios RAGAS del documento |

---

### 6.4 API REST — `api/routes/`

Hay cuatro routers, cada uno mapeado a un **Epígrafe (EP)** del diseño del sistema:

#### EP-001: Ingesta (`/api/v1/ingesta/`)

| Método | Ruta | HU | Descripción |
|---|---|---|---|
| `POST` | `/cargar` | HU-001 | Sube un PDF, valida, y arranca el pipeline automáticamente |
| `GET` | `/{doc_id}/estructura` | HU-002 | Retorna las secciones detectadas en el documento |
| `GET` | `/{doc_id}/progreso` | HU-003 | **SSE**: stream en tiempo real del porcentaje de avance |

El endpoint de progreso usa `StreamingResponse` con `media_type="text/event-stream"` (Server-Sent Events). El generador asíncrono lee el archivo JSON de progreso del disco cada 500 ms y envía el estado mientras el porcentaje cambia. Envía `": keep-alive\n\n"` periódicamente para mantener la conexión TCP activa. Termina automáticamente cuando el estado es `COMPLETADO` o `ERROR`.

**Persistencia del progreso en disco:** El progreso se guarda como JSON en `./data/progreso/{documento_id}.json`. Esto permite que el SSE funcione aunque la conexión se corte y reconecte, y que sobreviva a reinicios del servidor (los archivos persisten).

#### EP-002: Grafo (`/api/v1/grafo/`)

| Método | Ruta | HU | Descripción |
|---|---|---|---|
| `GET` | `/{doc_id}/referencias` | HU-004 | Lee referencias de Neo4j (no llama al LLM de nuevo) |
| `GET` | `/{doc_id}/citas` | HU-005 | Lee citas de Neo4j (no llama al LLM de nuevo) |
| `GET` | `/{doc_id}/resumen` | HU-006 | Estadísticas del grafo: nodos, relaciones, densidad |

#### EP-003: Recuperación (`/api/v1/recuperacion/`)

| Método | Ruta | HU | Descripción |
|---|---|---|---|
| `GET` | `/{doc_id}/motor/estado` | HU-007 | ¿Está listo el motor? Chunks en Supabase + citas vinculadas en Neo4j |
| `POST` | `/{doc_id}/motor/consultar` | HU-008 | Búsqueda libre: dado un texto, retorna fragmentos relevantes |
| `GET` | `/{doc_id}/motor/cita/{cita_id}` | HU-008/009 | Evidencia completa de una cita específica |
| `GET` | `/{doc_id}/motor/evidencia/{cita_id}` | HU-009 | Alias semántico del anterior, mismo resultado |

#### EP-004: Auditoría (`/api/v1/auditoria/`)

| Método | Ruta | HU | Descripción |
|---|---|---|---|
| `POST` | `/{doc_id}/auditar` | HU-010 | Ejecuta la auditoría completa, emite veredictos con GPT-4o-mini |
| `GET` | `/{doc_id}/veredictos` | HU-010 | Lee veredictos ya calculados de Neo4j |
| `GET` | `/{doc_id}/alertas` | HU-011 | Inconsistencias estructurales |
| `GET` | `/{doc_id}/alertas/alucinaciones` | HU-012 | Citas que el sistema no pudo verificar |
| `POST` | `/{doc_id}/evaluar-ragas` | EP-RAGAS | Calcula métricas RAGAS para las citas con evidencia |
| `GET` | `/{doc_id}/metricas` | EP-RAGAS | Promedios RAGAS del documento |
| `GET` | `/{doc_id}/metricas/exportar` | EP-RAGAS | Descarga Excel con scores por cita |

---

### 6.5 Servicios de ingesta — `services/ingesta/`

#### `pdf_service.py` — `PDFExtractionService`

**Responsabilidades:**

1. **`validar_pdf(contenido, nombre)`:** Verifica tres condiciones:
   - La extensión termina en `.pdf`
   - El tamaño no supera el límite configurado (por defecto 10 MB)
   - Los primeros 4 bytes son `%PDF` (magic bytes del formato PDF)

2. **`extraer_texto(ruta_pdf)`:** Usa `PyMuPDF (fitz)` para contar páginas y `pymupdf4llm` para convertir el PDF a Markdown. Implementa **caché en disco**: si ya existe un archivo `.md` con el mismo nombre que el PDF, lo reutiliza sin re-procesar. Esto es importante porque la extracción puede tomar varios segundos en documentos largos. Lanza `PDFNoProcessableError` si el resultado tiene menos de 100 caracteres (probablemente un PDF escaneado sin texto digital).

3. **`detectar_secciones(texto_md, num_paginas, documento_id)`:** Recorre línea por línea el Markdown buscando headings (`#`). Cuando encuentra un heading que coincide con alguno de los patrones de sección (expresiones regulares en `PATRONES_SECCIONES`), lo clasifica como un tipo de sección. Las páginas se estiman proporcionalmente según la posición en el texto.

**Clase de error personalizada `PDFNoProcessableError`:** Lleva tres atributos: `codigo` (ej. `ARCHIVO_DEMASIADO_GRANDE`), `mensaje` legible por el usuario, y `accion_sugerida`. Los routers capturan esta excepción y la transforman en un `HTTPException 422` con ese payload estructurado.

---

### 6.6 Servicios de grafo — `services/grafo/`

#### `neo4j_service.py` — `Neo4jService`

Gestiona la **conexión con Neo4j AuraDB**. Usa reconexión automática: la propiedad `driver` verifica la conectividad en cada acceso y reconecta si la conexión está caída. Al arrancar el servidor, `inicializar_schema()` crea:
- **4 constraints UNIQUE** (en `Documento.id`, `Referencia.id`, `Cita.id`, `Autor.nombre_normalizado`)
- **4 índices** (en atributos frecuentemente consultados: `Autor.nombre`, `Referencia.titulo`, etc.)

Esto garantiza que no se dupliquen nodos por operaciones concurrentes y que las queries sean eficientes.

#### `extraccion_service.py` — `EntidadExtractionService`

Es el servicio más complejo del backend. Coordina dos tareas de extracción con LLM:

**Extracción de referencias (`extraer_referencias`):**
1. Descarta la sección de Anexos (regex `_RE_INICIO_ANEXOS`) para evitar falsos positivos
2. Localiza el inicio de la sección de Referencias con regex multicapa
3. Divide el texto de referencias en **bloques de máximo 7000 caracteres**, respetando párrafos completos y oraciones (para no cortar a la mitad una referencia)
4. Por cada bloque llama a GPT-4o-mini con el prompt `SYSTEM_REFERENCIAS` que especifica exactamente el formato JSON esperado
5. Parsea la respuesta con `_parsear_json()` que maneja tres estrategias: JSON array, JSON por líneas, y JSON truncado (para recuperarse de respuestas mal formateadas del LLM)

**Extracción de citas (`extraer_citas`):**
1. También descarta Anexos y excluye la sección de Referencias del cuerpo a procesar
2. Aplica **regex pre-filtro** (`_detectar_citas_regex`) para encontrar candidatos APA antes de llamar al LLM. Esto sirve de "pista" al LLM y reduce tokens
3. Divide el cuerpo en bloques de 7000 chars y llama al LLM con `SYSTEM_CITAS`, que es un prompt muy detallado que especifica exactamente cómo debe ser el `fragmento_oracion` para citas parentéticas y narrativas
4. **Deduplicación:** elimina duplicados exactos por `(texto_cita, pagina)`

El `fragmento_oracion` es la oración completa donde aparece la cita, sin cruzar hacia la siguiente cita. Es crucial para la auditoría: es la "afirmación del tesista" que el LLM evaluará.

#### `grafo_carga_service.py` — `GrafoCargaService`

Traduce las entidades Python a operaciones **Cypher (MERGE)** en Neo4j. El patrón `MERGE` garantiza idempotencia: si el nodo ya existe, lo actualiza; si no, lo crea.

**`vincular_citas(documento_id)`:** Este método es el más algorítmicamente complejo. Vincula cada `Cita` con su `Referencia` usando tres estrategias en cascada:

1. **Apellido exacto + año** → confianza 0.95 (ej.: "García (2021)" con "García, J.")
2. **Apellido parcial + año** → confianza 0.75 (ej.: "García" coincide con "García-López")
3. **Solo año** (si hay exactamente una referencia de ese año) → confianza 0.50

La normalización de apellidos usa `unidecode` para comparar sin tildes ni mayúsculas (evita que "García" y "Garcia" sean tratados como personas distintas).

---

### 6.7 Servicios externos — `services/externo/`

#### `verificacion_service.py` — `VerificacionService`

**Orquestador** que conecta los tres servicios externos en secuencia para cada referencia:

```
CrossRef → DOI + abstract
    ↓ (si hay DOI)
Unpaywall → PDF gratuito → texto completo
    ↓ (si hay texto)
EmbeddingService → chunks + embeddings → Supabase
    ↓
Neo4j → actualizar Referencia con doi_verificado, titulo_oficial, nivel_confianza
```

Por defecto verifica solo las **primeras 10 referencias** del documento (`max_referencias=10`) para controlar el tiempo de procesamiento.

#### `crossref_service.py` — `CrossRefService`

Consulta la **CrossRef REST API** (gratuita, +130 millones de papers). La búsqueda se construye con el título y el primer autor. Si el usuario configuró un año, se añade un filtro `from-pub-date:YYYY,until-pub-date:YYYY`. Tiene reintentos con backoff exponencial (espera 2, 4, 8 segundos entre intentos). Calcula un **score de coincidencia** por Jaccard entre las palabras del título buscado y el título encontrado.

#### `unpaywall_service.py` — `UnpaywallService`

Dada una DOI, consulta **Unpaywall** para encontrar si existe una versión de acceso abierto (preprint, repositorio institucional, etc.). Si la encuentra, descarga el PDF y lo convierte a texto con `pymupdf4llm`. El texto se cachea en disco (`./data/processed/papers/{doi_normalizado}.txt`) para no volver a descargarlo en auditorías posteriores. Solo se conserva el `.txt`; el PDF se borra para ahorrar espacio.

#### `embedding_service.py` — `EmbeddingService`

Gestiona el proceso completo de **indexado semántico de papers**:

1. **Truncar referencias del paper**: Antes de indexar, elimina la sección de referencias del propio paper (para que el sistema no confunda referencias con contenido). Usa tres señales: encabezado explícito de referencias, sección "Acknowledgements/Funding", y patrón Vancouver (3+ líneas consecutivas "N. Apellido..."). Solo busca en la segunda mitad del documento (evita falsos positivos).

2. **Chunking**: Divide el texto en fragmentos por orden de prioridad:
   - Form-feed `\f` (saltos de página PDF)
   - Marcadores textuales: "Page N", "--- Page N ---"
   - Por tamaño (máx. 1500 chars), cortando en párrafos o puntos

3. **Filtrado de chunks basura**: Descarta chunks que tengan >40% de URLs, contengan frases de boilerplate editorial ("springer nature remains neutral", "conflict of interest"), o tengan menos de 100 caracteres reales. También descarta chunks de portada (emails + ORCIDs + afiliaciones institucionales).

4. **Embeddings**: Usa `sentence-transformers` con el modelo `all-MiniLM-L6-v2` (corre completamente local, sin API, 384 dimensiones). El modelo se carga en memoria de forma lazy (solo cuando se necesita por primera vez).

5. **Almacenamiento**: Guarda cada chunk en Supabase `papers_chunks` con su embedding, DOI, página, y metadatos.

---

### 6.8 Vector store — `services/vectorstore/`

#### `supabase_service.py` — `SupabaseVectorService`

Wrapper sobre `psycopg2` para la tabla `papers_chunks` en Supabase (PostgreSQL + pgvector). Operaciones:

- **`buscar_similares(embedding, doi_normalizado, top_k)`**: Usa el operador `<=>` de pgvector (distancia coseno). Puede filtrar por DOI específico o buscar en todos los papers. La similitud se calcula como `1 - distancia_coseno`.
- **`indexar_chunks(chunks)`**: `INSERT ... ON CONFLICT DO UPDATE` — idempotente, re-indexar el mismo paper no crea duplicados.
- **`eliminar_chunks_por_doi(doi_normalizado)`**: Limpia los chunks existentes antes de re-indexar (para cuando se actualiza un paper).
- **`paper_ya_indexado(doi_normalizado)`**: Verificación rápida antes de re-indexar.

---

### 6.9 Clientes LLM — `services/llm/`

Los dos clientes (`openai_client.py` y `groq_client.py`) implementan **la misma interfaz**: un método `completar(system_prompt, user_prompt) → str` con reintentos automáticos (backoff exponencial, máximo 3 intentos). Ambos usan `temperature=0.0` para obtener respuestas **deterministas** (mínima variabilidad entre llamadas).

El cliente **activo en producción** es el de **OpenAI** (`llm_service` importado en los servicios que necesitan LLM). El cliente Groq es idéntico en interfaz y sirve como alternativa (se puede switchear cambiando el import).

El cliente OpenAI también registra el **costo estimado en USD** por llamada (`total_tokens * 0.00000015`), útil para monitorear costos operacionales.

---

### 6.10 Motor de recuperación — `services/recuperacion/`

#### `recuperacion_service.py` — `RecuperacionService`

Implementa la lógica central de **GraphRAG**: combina traversal de grafo con búsqueda vectorial.

**`consultar_cita(documento_id, cita_id)`:**

```
Neo4j traversal:
  Cita → [:CITA_A] → Referencia → DOI + autores + título

Supabase búsqueda semántica:
  encode(texto_cita) → embedding 384d
  SELECT ... FROM papers_chunks WHERE doi_normalizado = ? ORDER BY embedding <=> ? LIMIT 3

Resultado:
  - Si hay fragmento: método = "hibrido"
  - Si hay referencia pero no fragmento: método = "solo_grafo"
  - Si no hay nada: método = "no_encontrado"
```

**`consultar_texto_libre(texto_consulta, n_resultados)`:** Búsqueda sin DOI específico, busca en todos los papers indexados. Cruza los fragmentos encontrados con las citas del documento por DOI.

---

### 6.11 Auditoría semántica — `services/auditoria/`

#### `auditoria_service.py` — `AuditoriaService`

**`auditar_documento(documento_id)`:** Itera todas las citas usando un **`ThreadPoolExecutor` con máximo 5 workers**. Esto permite auditar varias citas en paralelo, reduciendo el tiempo total cuando hay muchas citas (cada llamada a GPT-4o-mini tarda ~2 segundos).

Por cada cita (`_auditar_cita`):
1. Si no tiene referencia vinculada → `NO_VERIFICABLE` (sin llamar al LLM)
2. Si el paper no está en Supabase → `NO_VERIFICABLE` (sin llamar al LLM)
3. Recupera el fragmento más similar del paper via `recuperacion_service`
4. Si no hay fragmento → `NO_VERIFICABLE`
5. Si hay fragmento → llama a GPT-4o-mini con:
   - La afirmación del tesista (`fragmento_oracion`)
   - La cita APA (`texto_cita`)
   - El fragmento del paper (`fragmento_relevante`)
6. Parsea el formato estructurado de respuesta del LLM: `VEREDICTO: X\nJUSTIFICACIÓN: Y`
7. Persiste el veredicto en Neo4j con `SET c.veredicto = ...`

**`detectar_inconsistencias(documento_id)`:**
- **Citas sin referencia**: citas que no tienen relación `CITA_A` en el grafo
- **Referencias sin citar**: referencias que no aparecen como destino de ningún `CITA_A`

**`evaluar_ragas_documento(doc_id)`:** Lee las citas que tienen `fragmento_evidencia` (del paso de auditoría) y las evalúa con el framework RAGAS. Los scores se persisten en Neo4j para no recalcular.

---

### 6.12 Evaluación RAGAS — `services/evaluacion/`

#### `ragas_service.py` — `RagasService`

Wrapper sobre la librería **RAGAS** que evalúa la calidad de la recuperación. Usa GPT-4o-mini como LLM evaluador y `text-embedding-3-small` de OpenAI para los embeddings de evaluación.

Para cada cita se evalúan **5 métricas** (escala 0-1):

| Métrica | Qué mide |
|---|---|
| `faithfulness` | ¿La respuesta (justificación) es fiel al contexto (fragmento del paper)? |
| `answer_relevancy` | ¿La respuesta es relevante a la pregunta (fragmento de oración)? |
| `context_precision` | ¿El contexto recuperado es preciso para responder la pregunta? |
| `context_recall` | ¿El contexto recuperado contiene la información necesaria? |
| `answer_correctness` | ¿La respuesta es correcta comparada con la referencia de verdad? |

Los inputs de RAGAS se mapean así:
- `question` → el `fragmento_oracion` (la afirmación del tesista)
- `answer` → la `justificacion` del LLM
- `contexts` → el `fragmento_evidencia` del paper
- `reference`/`ground_truth` → el mismo `fragmento_evidencia`

---

## 7. Modelo de datos Neo4j (grafo)

### Nodos

| Etiqueta | Propiedad clave | Descripción |
|---|---|---|
| `Documento` | `id` (UUID) | El paper/tesis subido al sistema |
| `Referencia` | `id` (UUID) | Una entrada de la lista de referencias bibliográficas |
| `Autor` | `nombre_normalizado` | Un autor académico (normalizado sin tildes, minúsculas) |
| `Cita` | `id` (UUID) | Una cita encontrada en el cuerpo del texto |

### Relaciones

| Relación | Origen → Destino | Descripción |
|---|---|---|
| `TIENE_REFERENCIA` | Documento → Referencia | El documento lista esta referencia en su bibliografía |
| `TIENE_CITA` | Documento → Cita | El documento contiene esta cita en su texto |
| `ESCRITO_POR` | Referencia → Autor | La referencia fue escrita por este autor |
| `CITA_A` | Cita → Referencia | Esta cita en el texto corresponde a esta referencia (con props: `confianza`, `metodo`) |

### Propiedades relevantes del nodo `Cita` (post-auditoría)

```
veredicto          = "VÁLIDA" | "DUDOSA" | "ALUCINADA" | "NO_VERIFICABLE"
justificacion      = texto generado por GPT-4o-mini
fragmento_evidencia= chunk del paper usado como evidencia
pagina_paper       = página del chunk en el paper citado
faithfulness       = float 0-1
answer_relevancy   = float 0-1
context_precision  = float 0-1
context_recall     = float 0-1
answer_correctness = float 0-1
```

### Visualización del grafo

```
(Documento)─[:TIENE_REFERENCIA]─►(Referencia)─[:ESCRITO_POR]─►(Autor)
     │                                 ▲
     └──────[:TIENE_CITA]─►(Cita)─[:CITA_A]──────────────────────┘
```

---

## 8. Modelo de datos Supabase (vector store)

### Tabla `papers_chunks`

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | `text` (PK) | `{doi_normalizado}_chunk_{idx}` |
| `doi` | `text` | DOI original del paper |
| `doi_normalizado` | `text` | DOI con `/` y `.` reemplazados por `_` |
| `titulo` | `text` | Título oficial del paper (de CrossRef) |
| `anio` | `text` | Año de publicación |
| `pagina` | `int` | Número de página/chunk (1-based) |
| `chunk_index` | `int` | Índice 0-based del chunk |
| `nivel_confianza` | `text` | `texto_completo`, `abstract`, o `cache` |
| `referencia_id` | `text` | UUID de la Referencia en Neo4j |
| `contenido` | `text` | Texto del fragmento |
| `embedding` | `vector(384)` | Embedding generado por all-MiniLM-L6-v2 |

**Índice:** `ivfflat` con distancia coseno sobre la columna `embedding`, para búsqueda ANN eficiente.

---

## 9. Tests

El proyecto tiene **tests unitarios** en `tests/unit/` y **scripts de diagnóstico** en `tests/`:

### Tests unitarios (`pytest`, sin servicios reales)

| Archivo | Qué prueba |
|---|---|
| `test_auditoria_service.py` | Verifica que `_auditar_cita` propaga `fragmento_oracion` al veredicto; todo mocked |
| `test_extraccion_service.py` | Regex de detección de citas APA: parentéticas, narrativas, múltiples, falsos positivos |
| `test_pdf_service.py` | Validación de PDF: extensión, tamaño, magic bytes |
| `test_embedding_service.py` | Chunking, filtrado de basura, detección de portadas |
| `test_recuperacion_service.py` | Construcción de nodos de grafo, flujo de recuperación |

Los tests usan `unittest.mock.patch` para reemplazar llamadas al LLM, Neo4j y Supabase. Esto permite ejecutarlos sin conexión a ningún servicio externo.

### Scripts de diagnóstico

- `diagnostico_chroma.py` — Verifica el estado del vector store (conteo de chunks, búsqueda de prueba)
- `diagnostico_citas.py` — Extrae y muestra las citas de un documento de prueba
- `diagnostico_ragas.py` — Ejecuta una evaluación RAGAS de prueba con datos ficticios
- `test_embedding_pipeline.py` — Prueba el pipeline completo de embeddings en un PDF real

El PDF de prueba usado en los tests es `tests/Informe de tesis_AlcaldeLavadoMatias.pdf` — una tesis real del autor del sistema.

---

## 10. Metodología de desarrollo detectada

El código presenta evidencia clara de **metodología ágil con Scrum** o una variante similar:

1. **Historias de Usuario (HU):** Cada endpoint lleva un tag explícito como `HU-001`, `HU-002`... hasta `HU-012`. Esto es la traza directa entre los requisitos del product backlog y el código entregado. En Scrum, cada HU corresponde a una unidad de valor para el usuario.

2. **Épicos (EP):** Los routers agrupan HUs bajo Épicos: `EP-001` (Ingesta), `EP-002` (Grafo), `EP-003` (Recuperación), `EP-004` (Auditoría). Esta jerarquía HU → Épico es característica del Scrum con backlog refinado.

3. **Requisitos no funcionales (RN):** Los comentarios mencionan `RN-002` (formato estándar de errores), `RN-005` (latencia < 3 segundos). Esto sugiere que el equipo trabajó con una especificación de requisitos formal separada.

4. **Incrementos progresivos:** La numeración secuencial de HUs indica que el sistema se construyó en sprints, añadiendo funcionalidad de forma incremental: primero la ingesta (HU-001 a HU-003), luego el grafo (HU-004 a HU-006), luego la recuperación (HU-007 a HU-009), finalmente la auditoría (HU-010 a HU-012).

5. **Tests por capa:** Los tests unitarios están organizados por servicio, con separación clara entre unit e integration, lo que es coherente con una definición de "done" que incluye cobertura de tests por sprint.

---

## 11. Decisiones de diseño relevantes

### ¿Por qué Neo4j + Supabase pgvector en lugar de solo un vector store?

El sistema es **GraphRAG**, no RAG puro. El grafo de Neo4j aporta información estructural que no está en los embeddings: la relación `CITA_A` establece qué referencia corresponde a cada cita antes de hacer la búsqueda vectorial. Esto permite restringir la búsqueda semántica al paper correcto (por DOI), evitando falsos positivos de papers de otros autores.

### ¿Por qué el pipeline corre en un thread separado?

FastAPI es asíncrono, pero las operaciones del pipeline (llamadas síncronas a OpenAI, Neo4j, CrossRef) bloquearían el event loop si se ejecutaran en el hilo principal. Al usar `threading.Thread`, el endpoint `/cargar` responde inmediatamente con el `documento_id` y el pipeline corre en segundo plano. El frontend sigue el progreso vía SSE.

### ¿Por qué el progreso se guarda en disco y no en memoria?

Si el servidor se reinicia, el estado en memoria se pierde. Con JSON en disco, el frontend puede reconectar el SSE y recuperar el estado real de la auditoría. También permite múltiples instancias del servidor leer el mismo estado (escalabilidad horizontal básica).

### ¿Por qué `temperature=0.0` en el LLM?

Para que los veredictos sean **reproducibles**. Con temperatura 0, el mismo input siempre produce el mismo output. En un sistema de auditoría académica, la variabilidad no es deseable: el mismo PDF debe producir los mismos veredictos en cada ejecución.

### ¿Por qué modelo `all-MiniLM-L6-v2` local en lugar de OpenAI Embeddings?

- **Costo cero** en generación de embeddings (se ejecuta en la CPU/GPU del servidor)
- **Sin latencia de red** para generar embeddings de los chunks (procesa en batch local)
- **384 dimensiones** → queries de similitud coseno más rápidas en Supabase
- El trade-off es que los embeddings para búsqueda son de OpenAI (`text-embedding-3-small`, 1536d) en la evaluación RAGAS, pero el motor de recuperación operacional usa el modelo local

### ¿Por qué verificar solo las primeras 10 referencias?

CrossRef + Unpaywall son APIs síncronas que pueden tardar varios segundos por referencia. Verificar 50+ referencias bloquearía el pipeline durante minutos. El límite de 10 es configurable en código (`max_referencias=10` en `verificacion_service`) y balancea cobertura vs. tiempo de respuesta.

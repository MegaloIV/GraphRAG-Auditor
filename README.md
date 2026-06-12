# GraphRAG-Auditor

Sistema de **auditoría semántica de documentos académicos** que utiliza tecnología GraphRAG (Graph Retrieval-Augmented Generation) para validar la consistencia entre citas, referencias bibliográficas y el contenido original de los papers citados. Detecta alucinaciones semánticas y distorsiones de información, asegurando que las referencias académicas mantengan fidelidad al contenido fuente.

> **Nota de migración:** El almacenamiento vectorial de embeddings migró de ChromaDB a **Supabase + pgvector**. ChromaDB ya no es una dependencia del proyecto. Antes de levantar el backend, crea la tabla `papers_chunks` en tu proyecto Supabase y configura las variables `SUPABASE_DB_*` en el `.env`.

---

## Descripción General

GraphRAG-Auditor automatiza el proceso de auditoría académica en un pipeline de extremo a extremo:

1. El usuario sube un documento PDF (tesis, artículo, informe).
2. El sistema extrae texto, detecta secciones, citas APA y referencias bibliográficas.
3. Construye un **grafo de conocimiento** en Neo4j que modela las relaciones entre documentos, citas, referencias y autores.
4. Verifica la existencia y metadatos de cada referencia contra la API pública de **CrossRef**.
5. Recupera fragmentos relevantes de los papers citados desde el almacén vectorial (**Supabase + pgvector**) mediante embeddings semánticos.
6. Un LLM (**GPT-4o-mini**) emite un veredicto por cada cita: `VÁLIDA`, `DUDOSA`, `ALUCINADA` o `NO_VERIFICABLE`.
7. Evalúa la calidad de la recuperación con las métricas del framework **RAGAS** (faithfulness, answer relevancy, context precision, context recall, answer correctness).
8. Presenta los resultados en una interfaz web interactiva y permite exportarlos a Excel.

---

## Tecnologías y Dependencias

### Backend

| Componente | Tecnología |
|---|---|
| Framework web | FastAPI 0.136 + Uvicorn 0.47 |
| Validación de datos | Pydantic 2.13 + pydantic-settings |
| Base de datos de grafos | Neo4j 6.2 |
| Almacén vectorial | Supabase PostgreSQL + pgvector 0.4 + psycopg2 |
| Extracción de PDF | PyMuPDF 1.24 + pymupdf4llm + pdfplumber |
| LLM principal | OpenAI GPT-4o-mini (openai 1.109) |
| LLM alternativo | Groq / LLaMA 3.1 (groq vía LangChain) |
| Embeddings | sentence-transformers 5.5 + ONNX Runtime |
| Evaluación RAG | RAGAS 0.1.21 |
| Framework LLM | LangChain 0.2 + langchain-openai |
| Grafos en memoria | NetworkX 3.6 |
| APIs externas | CrossRef (gratuita) + Unpaywall |
| Logging estructurado | structlog 25.5 |
| Testing | pytest |

### Frontend

| Componente | Tecnología |
|---|---|
| Framework UI | React 19.2 |
| Build tool | Vite 8.0 |
| Cliente HTTP | Axios 1.16 |
| Iconos | Lucide React 1.14 |
| Carga de archivos | React Dropzone 15.0 |
| Linter | ESLint 10.2 |

### Infraestructura

| Servicio | Uso |
|---|---|
| Neo4j AuraDB | Grafo de conocimiento (cloud managed) |
| Supabase | Almacén vectorial con pgvector |
| Railway | Hosting del backend |
| Vercel | Hosting del frontend |

---

## Estructura del Proyecto

```
GraphRAG-Auditor/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── ingesta.py        # HU-001/002/003: carga PDF, estructura, progreso SSE
│   │   │       ├── grafo.py          # HU-004/005/006: referencias, citas, resumen del grafo
│   │   │       ├── recuperacion.py   # HU-007/008/009: motor de búsqueda semántica
│   │   │       └── auditoria.py      # HU-010/011/012: veredictos, alertas, RAGAS, Excel
│   │   ├── core/
│   │   │   ├── config.py             # Configuración centralizada (variables de entorno)
│   │   │   └── logging.py            # Setup de structlog
│   │   ├── models/
│   │   │   ├── ingesta.py            # Schemas Pydantic de ingesta
│   │   │   ├── grafo.py              # Schemas de referencias y citas
│   │   │   └── auditoria.py          # Schemas de veredictos y métricas
│   │   ├── services/
│   │   │   ├── ingesta/
│   │   │   │   └── pdf_service.py    # Extracción de texto y detección de secciones (PyMuPDF)
│   │   │   ├── grafo/
│   │   │   │   ├── extraccion_service.py  # Extracción de refs y citas APA con LLM
│   │   │   │   ├── grafo_carga_service.py # Carga y consultas del grafo Neo4j
│   │   │   │   └── neo4j_service.py       # Cliente Neo4j y schema de constraints
│   │   │   ├── recuperacion/
│   │   │   │   └── recuperacion_service.py # Búsqueda híbrida: grafo + vectores
│   │   │   ├── auditoria/
│   │   │   │   └── auditoria_service.py   # Veredictos con GPT-4o-mini + detección inconsistencias
│   │   │   ├── evaluacion/
│   │   │   │   └── ragas_service.py       # Evaluación con framework RAGAS
│   │   │   ├── vectorstore/
│   │   │   │   └── supabase_service.py    # CRUD de embeddings en pgvector
│   │   │   ├── externo/
│   │   │   │   ├── crossref_service.py    # Verificación de DOIs vía CrossRef API
│   │   │   │   ├── unpaywall_service.py   # Obtención de PDFs en abierto
│   │   │   │   ├── embedding_service.py   # Generación de embeddings (sentence-transformers)
│   │   │   │   └── verificacion_service.py # Orquestador de verificación externa
│   │   │   └── llm/
│   │   │       ├── openai_client.py       # Cliente OpenAI (GPT-4o-mini)
│   │   │       └── groq_client.py         # Cliente Groq (LLaMA)
│   │   ├── utils/
│   │   └── main.py                   # Punto de entrada FastAPI + lifespan + CORS
│   ├── data/
│   │   ├── uploads/                  # PDFs recibidos (nombrados por UUID)
│   │   └── progreso/                 # JSON de progreso del pipeline (persistencia en disco)
│   ├── logs/
│   │   └── app.log
│   ├── tests/
│   │   ├── unit/                     # Tests unitarios con pytest
│   │   │   ├── test_auditoria_service.py
│   │   │   ├── test_embedding_service.py
│   │   │   ├── test_extraccion_service.py
│   │   │   ├── test_pdf_service.py
│   │   │   └── test_recuperacion_service.py
│   │   ├── diagnostico_chroma.py     # Script de diagnóstico del almacén vectorial
│   │   ├── diagnostico_citas.py      # Script de diagnóstico de citas
│   │   ├── diagnostico_ragas.py      # Script de diagnóstico RAGAS
│   │   └── test_embedding_pipeline.py
│   ├── requirements.txt
│   └── .env
│
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   │   └── client.js             # Cliente Axios con todos los endpoints agrupados
│   │   ├── components/
│   │   │   ├── auditoria/
│   │   │   │   ├── AlertasAlucinaciones.jsx   # Citas no verificables
│   │   │   │   ├── AlertasInconsistencias.jsx # Citas sin ref / refs sin citar
│   │   │   │   ├── ListaVeredictos.jsx        # Tabla de veredictos por cita
│   │   │   │   └── MetricasRagas.jsx          # Dashboard de métricas RAGAS
│   │   │   ├── grafo/
│   │   │   │   ├── ListaCitas.jsx     # Citas detectadas en el texto
│   │   │   │   ├── ListaReferencias.jsx # Referencias bibliográficas extraídas
│   │   │   │   └── ResumenGrafo.jsx   # Estadísticas del grafo construido
│   │   │   ├── ingesta/
│   │   │   │   ├── ZonaCarga.jsx          # Dropzone de carga de PDF
│   │   │   │   ├── ProgresoAuditoria.jsx  # Barra de progreso SSE
│   │   │   │   └── EstructuraDocumento.jsx # Secciones detectadas del PDF
│   │   │   ├── recuperacion/
│   │   │   │   └── EstadoMotor.jsx    # Indicador de preparación del motor
│   │   │   └── ui/
│   │   │       ├── Badge.jsx          # Etiqueta de estado/veredicto
│   │   │       ├── BarraProgreso.jsx  # Componente de progreso
│   │   │       ├── Card.jsx           # Contenedor de sección
│   │   │       └── Navbar.jsx         # Barra de navegación
│   │   ├── pages/
│   │   │   ├── PaginaInicio.jsx       # Landing: carga de documento
│   │   │   └── PaginaAuditoria.jsx    # Dashboard completo de resultados
│   │   ├── store/
│   │   │   └── accentStore.js         # Gestión del color de acento (tema)
│   │   ├── App.jsx                    # Componente raíz con enrutamiento simple
│   │   └── main.jsx
│   ├── public/
│   ├── package.json
│   ├── vite.config.js
│   ├── eslint.config.js
│   └── .env
│
├── LICENSE
└── README.md
```

### Flujo del Pipeline de Auditoría

```
PDF subido
    │
    ▼
[10%] Extracción de texto (PyMuPDF → Markdown)
    │
    ▼
[25%] Detección de secciones del documento
    │
    ▼
[40%] Extracción de referencias APA (GPT-4o-mini)
    │
    ▼
[55%] Extracción de citas en el texto (regex + LLM)
    │
    ▼
[70%] Construcción del grafo en Neo4j
      (nodos: Documento, Referencia, Cita, Autor)
    │
    ▼
[78%] Vinculación de citas ↔ referencias
    │
    ▼
[85%] Verificación de referencias en CrossRef (DOI)
    │
    ▼
[100%] Pipeline completado — auditoría semántica disponible
    │
    ▼
(Manual) POST /auditar → GPT-4o-mini emite veredicto por cita
    │
    ▼
(Manual) POST /evaluar-ragas → scores RAGAS por cita
```

---

## Instalación

### Requisitos previos

- **Python** 3.10 o superior
- **Node.js** 18 o superior
- Cuenta en **Neo4j AuraDB** (o instancia Neo4j local 5.x)
- Proyecto en **Supabase** con extensión `pgvector` habilitada
- API key de **OpenAI** (GPT-4o-mini)
- API key de **Groq** (opcional, como LLM alternativo)

### Backend

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/GraphRAG-Auditor.git
cd GraphRAG-Auditor/backend

# 2. Crear entorno virtual
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env             # editar con tus credenciales

# 5. Crear tabla de vectores en Supabase
# Ejecuta en el SQL Editor de Supabase:
# CREATE EXTENSION IF NOT EXISTS vector;
# CREATE TABLE papers_chunks (
#   id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
#   doi         TEXT,
#   chunk_index INTEGER,
#   texto       TEXT,
#   embedding   vector(384)
# );
# CREATE INDEX ON papers_chunks USING ivfflat (embedding vector_cosine_ops);
```

### Frontend

```bash
cd GraphRAG-Auditor/frontend

# Instalar dependencias
npm install

# Configurar la URL del backend
cp .env.example .env
# VITE_API_BASE_URL=http://localhost:8000/api/v1
```

---

## Uso

### Iniciar el backend

```bash
cd backend
uvicorn app.main:app --reload --reload-dir app
# Disponible en http://localhost:8000
# Documentación interactiva en http://localhost:8000/docs
```

### Iniciar el frontend

```bash
cd frontend
npm run dev
# Disponible en http://localhost:5173
```

### Flujo de uso básico

1. Abre el frontend en `http://localhost:5173`.
2. Arrastra o selecciona un PDF académico en la zona de carga.
3. El sistema inicia el pipeline automáticamente; sigue el progreso en tiempo real.
4. Al completarse, navega a las pestañas para ver referencias, citas y el resumen del grafo.
5. Haz clic en **"Auditar"** para que el LLM emita veredictos por cada cita.
6. Opcionalmente, haz clic en **"Evaluar RAGAS"** para obtener métricas de calidad de la recuperación.
7. Descarga el reporte en Excel desde la sección de métricas.

---

## Variables de Entorno

### Backend (`backend/.env`)

| Variable | Descripción | Ejemplo |
|---|---|---|
| `APP_ENV` | Entorno de ejecución | `development` |
| `APP_PORT` | Puerto del servidor | `8000` |
| `OPENAI_API_KEY` | Clave API de OpenAI | `sk-proj-...` |
| `OPENAI_MODEL` | Modelo de OpenAI a usar | `gpt-4o-mini` |
| `GROQ_API_KEY` | Clave API de Groq (opcional) | `gsk_...` |
| `GROQ_MODEL` | Modelo de Groq | `llama-3.1-8b-instant` |
| `NEO4J_URI` | URI de conexión a Neo4j | `neo4j+s://xxxx.databases.neo4j.io` |
| `NEO4J_USERNAME` | Usuario Neo4j | `neo4j` |
| `NEO4J_PASSWORD` | Contraseña Neo4j | `...` |
| `NEO4J_DATABASE` | Base de datos Neo4j | `neo4j` |
| `SUPABASE_DB_HOST` | Host PostgreSQL de Supabase | `aws-0-us-east-1.pooler.supabase.com` |
| `SUPABASE_DB_PORT` | Puerto PostgreSQL | `5432` |
| `SUPABASE_DB_NAME` | Nombre de la base de datos | `postgres` |
| `SUPABASE_DB_USER` | Usuario PostgreSQL | `postgres.xxxx` |
| `SUPABASE_DB_PASSWORD` | Contraseña PostgreSQL | `...` |
| `MAX_PDF_SIZE_MB` | Tamaño máximo de PDF en MB | `10` |
| `UPLOAD_DIR` | Directorio de PDFs subidos | `./data/uploads` |
| `PROCESSED_DIR` | Directorio de archivos procesados | `./data/processed` |
| `LOG_LEVEL` | Nivel de logging | `INFO` |
| `CROSSREF_EMAIL` | Email para la API de CrossRef | `tu@email.com` |

### Frontend (`frontend/.env`)

| Variable | Descripción | Ejemplo |
|---|---|---|
| `VITE_API_BASE_URL` | URL base del backend | `http://localhost:8000/api/v1` |

---

## Scripts Disponibles

### Frontend (`package.json`)

| Script | Comando | Descripción |
|---|---|---|
| `dev` | `npm run dev` | Inicia el servidor de desarrollo con HMR |
| `build` | `npm run build` | Genera el build de producción en `dist/` |
| `preview` | `npm run preview` | Previsualiza el build de producción |
| `lint` | `npm run lint` | Ejecuta ESLint sobre el código fuente |

### Backend

```bash
# Servidor de desarrollo con recarga automática
uvicorn app.main:app --reload --reload-dir app

# Tests unitarios
pytest tests/unit/

# Tests con informe de cobertura
pytest tests/unit/ --cov=app --cov-report=term-missing

# Scripts de diagnóstico
python tests/diagnostico_chroma.py
python tests/diagnostico_citas.py
python tests/diagnostico_ragas.py
```

---

## API / Endpoints

Todas las rutas están bajo el prefijo `/api/v1`. La documentación interactiva (Swagger UI) está disponible en `/docs`.

### Health

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Información del servidor |
| `GET` | `/health` | Verificación de salud |

### Ingesta (`/api/v1/ingesta`)

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/ingesta/cargar` | Carga un PDF y arranca el pipeline de auditoría en background. Retorna `documento_id`. |
| `GET` | `/ingesta/{doc_id}/estructura` | Devuelve las secciones detectadas (introducción, metodología, referencias, etc.) |
| `GET` | `/ingesta/{doc_id}/progreso` | Stream SSE con el porcentaje y mensaje de progreso del pipeline |

### Grafo (`/api/v1/grafo`)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/grafo/{doc_id}/referencias` | Lista las referencias bibliográficas APA extraídas y almacenadas en Neo4j |
| `GET` | `/grafo/{doc_id}/citas` | Lista las citas encontradas en el cuerpo del texto (parentéticas y narrativas) |
| `GET` | `/grafo/{doc_id}/resumen` | Resumen del grafo: total de nodos, relaciones y densidad |

### Recuperación (`/api/v1/recuperacion`)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/recuperacion/{doc_id}/motor/estado` | Indica si el motor de búsqueda semántica está listo (chunks indexados + citas vinculadas) |
| `POST` | `/recuperacion/{doc_id}/motor/consultar` | Búsqueda libre: dado un texto, retorna los fragmentos de papers más similares |
| `GET` | `/recuperacion/{doc_id}/motor/cita/{cita_id}` | Evidencia completa de una cita: fragmento del paper + nodos del grafo + score de similitud |
| `GET` | `/recuperacion/{doc_id}/motor/evidencia/{cita_id}` | Alias semántico del endpoint anterior, orientado a la ruta de evidencia en el grafo |

### Auditoría (`/api/v1/auditoria`)

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/auditoria/{doc_id}/auditar` | Audita todas las citas con GPT-4o-mini. Emite y persiste veredictos en Neo4j |
| `GET` | `/auditoria/{doc_id}/veredictos` | Devuelve los veredictos ya calculados (`VÁLIDA`, `DUDOSA`, `ALUCINADA`, `NO_VERIFICABLE`) |
| `GET` | `/auditoria/{doc_id}/alertas` | Inconsistencias estructurales: citas sin referencia y referencias no citadas |
| `GET` | `/auditoria/{doc_id}/alertas/alucinaciones` | Citas que el sistema no pudo verificar por falta de evidencia |
| `POST` | `/auditoria/{doc_id}/evaluar-ragas` | Evalúa con RAGAS las citas que tienen fragmento de evidencia disponible |
| `GET` | `/auditoria/{doc_id}/metricas` | Promedios de métricas RAGAS del documento |
| `GET` | `/auditoria/{doc_id}/metricas/exportar` | Descarga un archivo Excel con los scores RAGAS individuales por cita |

### Veredictos posibles

| Veredicto | Significado |
|---|---|
| `VÁLIDA` | La cita es fiel al fragmento del paper encontrado |
| `DUDOSA` | La cita exagera, simplifica en exceso o tergiversa parcialmente la fuente |
| `ALUCINADA` | La cita afirma algo que el fragmento no dice o contradice directamente |
| `NO_VERIFICABLE` | El sistema no encontró evidencia suficiente para emitir un veredicto |

---


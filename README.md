# GraphRAG-Auditor

Sistema de **auditoría semántica de citas y referencias académicas (APA 7.ª)**. Recibe la tesis en PDF, extrae sus citas y referencias, construye un grafo de conocimiento y verifica —con evidencia del paper original— que cada cita sea fiel a su fuente. El resultado es un veredicto por cita (**Respaldada / Refutada / Sin evidencia**), alertas de inconsistencias y un informe descargable en Excel.

## Cómo funciona (en una pasada)

```
PDF → Extracción (LLM) → Revisión humana (PDF + cartillas editables) → Grafo Neo4j
                                                                          ↓
                      Asociación de fuentes (DOI / Zotero / PDF manual)
                                                                          ↓
                Recuperación híbrida (GraphRAG) → Auditoría LLM → Veredictos + Informe
```

- **El humano siempre está en el medio**: antes de verificar nada, el usuario revisa y corrige lo extraído sobre el PDF real, con salto exacto a la página de cada cita.
- **Sin adivinar fuentes**: la verificación automática usa solo el DOI; las referencias sin DOI se resuelven importando la colección de Zotero (.ris/.zip) o subiendo el PDF.
- **Un documento a la vez**: no hay procesamiento por lotes.

## Stack

| Capa | Tecnología |
|---|---|
| API | FastAPI + Uvicorn (Python 3.11) |
| Grafo de conocimiento | Neo4j (AuraDB) |
| Vector store | Supabase Postgres + pgvector (`text-embedding-3-small`, 1536 d) |
| Archivos y resultados | Supabase Storage |
| LLM de extracción/auditoría | OpenAI `gpt-5.4-mini` (Groq como cliente alternativo) |
| Verificación externa | CrossRef (por DOI) + Unpaywall (PDFs open access) |
| Calidad interna | RAGAS (faithfulness, answer relevancy, context precision) |
| Evaluación vs experto | scikit-learn (Kappa de Cohen, F1, matriz de confusión) |
| Frontend | React 19 + Vite, react-router 7, react-pdf, react-force-graph-2d |

## Estructura del repositorio

```
backend/
  app/
    api/routes/       # ingesta, grafo, recuperacion, auditoria, evaluacion
    core/             # config (env), logging
    models/           # Pydantic: ingesta, grafo, auditoria, evaluacion
    services/         # ingesta, grafo, externo, evaluacion, storage, vectorstore, llm
  sql/                # schema reproducible de Supabase (pgvector + progreso)
  Dockerfile          # imagen lista para Cloud Run (puerto $PORT)
frontend/
  src/                # pages, components, hooks, api, lib, store
docs/                 # documentación técnica (ver abajo)
```

## Ejecución en local

**Prerequisitos:** Python 3.11, Node 20+, y credenciales de: Neo4j AuraDB, Supabase (Postgres + Storage), OpenAI y Groq.

### Backend

```bash
cd backend
python -m venv venv && venv\Scripts\activate    # Windows (o source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                          # y rellena las credenciales
uvicorn app.main:app --reload --port 8000
```

- API en `http://localhost:8000` · Swagger en `http://localhost:8000/docs`.
- Provisión inicial de Supabase: ejecuta `backend/sql/supabase_schema.sql` en el SQL Editor y crea el bucket privado `graphrag-auditor` (detalle en [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md)).

### Frontend

```bash
cd frontend
npm install
copy .env.example .env    # VITE_API_BASE_URL=http://localhost:8000/api/v1
npm run dev
```

- App en `http://localhost:5173`.
- Apartado de administración (no enlazado desde el flujo): `/admin/ragas` y `/admin/evaluacion`.

## Despliegue

- **Backend → Google Cloud Run** (el `Dockerfile` ya está preparado):

```bash
gcloud run deploy graphrag-auditor-api \
  --source backend \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi --cpu 2 --timeout 3600 \
  --no-cpu-throttling \
  --env-vars-file env.yaml
```

- **Redesplegar** = volver a ejecutar el mismo comando (Cloud Run crea una nueva revisión y rota el tráfico solo).
- **Frontend → Vercel** con `VITE_API_BASE_URL` apuntando a la URL de Cloud Run.

La guía completa (env vars, por qué `--no-cpu-throttling` es obligatorio, CORS, Secret Manager, rollback) está en **[docs/DESPLIEGUE.md](docs/DESPLIEGUE.md)**.

## Documentación técnica

| Documento | Contenido |
|---|---|
| [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) | Visión general, componentes, flujo de datos, estados del documento, decisiones de diseño |
| [docs/BACKEND.md](docs/BACKEND.md) | Servicios por capa, pipelines, almacenes de datos, convenciones |
| [docs/API.md](docs/API.md) | Referencia completa de endpoints (request/response) |
| [docs/FRONTEND.md](docs/FRONTEND.md) | Rutas, fases, componentes, hooks, sistema de estilos |
| [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md) | Local, Google Cloud Run, Vercel, redespliegue y rollback |

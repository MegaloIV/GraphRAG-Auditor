# Despliegue

## 0. Servicios externos (una sola vez)

| Servicio | Qué provisionar |
|---|---|
| **Neo4j AuraDB** | Instancia (el free tier sirve). Anotar URI, usuario y password |
| **Supabase** | Proyecto con: tabla `papers_chunks` + índices (ejecutar `backend/sql/supabase_schema.sql` en el SQL Editor), tabla `auditoria_progreso` (la crea la app al arrancar, el SQL la incluye por si acaso) y **bucket privado** `graphrag-auditor` en Storage |
| **OpenAI** | API key (modelo `gpt-5.4-mini` + embeddings `text-embedding-3-small`) |
| **Groq** | API key (cliente alternativo de extracción) |
| **CrossRef/Unpaywall** | Solo requieren un email de contacto (`CROSSREF_EMAIL`) |

Variables de entorno del backend: la lista completa y comentada está en `backend/.env.example`.

## 1. Ejecución local

```bash
# Backend
cd backend
python -m venv venv && venv\Scripts\activate     # o source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                            # rellenar credenciales
uvicorn app.main:app --reload --port 8000

# Frontend (otra terminal)
cd frontend
npm install
copy .env.example .env                            # VITE_API_BASE_URL=http://localhost:8000/api/v1
npm run dev                                       # http://localhost:5173
```

## 2. Backend en Google Cloud Run

El `backend/Dockerfile` ya está preparado: instala torch CPU-only y arranca uvicorn en `$PORT` (Cloud Run inyecta 8080).

### Primer despliegue

```bash
gcloud auth login
gcloud config set project TU_PROYECTO

# env.yaml con las variables (mismo contenido que .env, formato YAML):
#   OPENAI_API_KEY: "sk-..."
#   OPENAI_MODEL: "gpt-5.4-mini"
#   NEO4J_URI: "neo4j+s://..."
#   ...
gcloud run deploy graphrag-auditor-api \
  --source backend \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi --cpu 2 \
  --timeout 3600 \
  --concurrency 20 \
  --no-cpu-throttling \
  --env-vars-file env.yaml
```

Flags que **no** son opcionales y por qué:

| Flag | Por qué |
|---|---|
| `--no-cpu-throttling` | El pipeline (extracción, verificación, Zotero) corre en **hilos de fondo después de responder la request**. Con el throttling por defecto, Cloud Run congela la CPU al terminar la respuesta y el pipeline se detiene a mitad. |
| `--timeout 3600` | El SSE de progreso es una conexión larga; también cubre la auditoría síncrona (minutos). |
| `--memory 2Gi` | PyMuPDF + torch CPU + embeddings sobre tesis de 150+ páginas. |

> `env.yaml` contiene secretos: **no lo commitees** (añádelo a `.gitignore`). La alternativa limpia es Secret Manager: `--set-secrets OPENAI_API_KEY=openai-key:latest,...`

### CORS

Tras el primer despliegue, añade el dominio del frontend a `allow_origins` en `backend/app/main.py` y redespliega. (Localhost y el dominio de Vercel actual ya están.)

### Redesplegar

```bash
gcloud run deploy graphrag-auditor-api --source backend --region us-central1
```

El mismo comando: Cloud Build reconstruye la imagen, crea una **nueva revisión** y mueve el tráfico automáticamente. Las variables/flags persisten entre revisiones (solo se pasan de nuevo si cambian).

Utilidades:

```bash
gcloud run services logs read graphrag-auditor-api --region us-central1   # logs
gcloud run revisions list --service graphrag-auditor-api --region us-central1
# rollback: enruta el tráfico a una revisión anterior
gcloud run services update-traffic graphrag-auditor-api \
  --region us-central1 --to-revisions REVISION_ANTERIOR=100
```

## 3. Frontend en Vercel

1. Importa el repo en Vercel con **Root Directory = `frontend`** (framework: Vite; build `npm run build`, output `dist`).
2. Variable de entorno: `VITE_API_BASE_URL = https://TU-SERVICIO.run.app/api/v1`.
3. Como es una SPA con rutas (`/doc/...`, `/admin/...`), añade `frontend/vercel.json` si no existe:

```json
{ "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }] }
```

**Redesplegar**: push a la rama conectada (deploy automático) o `vercel --prod`. Si cambia la URL del backend, actualiza `VITE_API_BASE_URL` en Vercel y redespliega (las vars de Vite se inyectan en build).

> Alternativa todo-Google: servir `frontend/dist` desde Firebase Hosting o un segundo servicio de Cloud Run con nginx. No hay nada en el código que dependa de Vercel.

## 4. Checklist post-despliegue

1. `GET https://TU-SERVICIO.run.app/health` → `{"status":"ok"}`.
2. Abrir el frontend: el indicador del topbar debe decir **"En línea"**.
3. Subir un PDF pequeño y ver la barra de progreso avanzar (valida hilos de fondo + SSE + Neo4j + Supabase).
4. En la fase de verificación, verificar una referencia con DOI (valida CrossRef/Unpaywall/pgvector/OpenAI).

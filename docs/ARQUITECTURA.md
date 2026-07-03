# Arquitectura

## Visión general

GraphRAG-Auditor es una aplicación de dos piezas — API FastAPI y SPA React — apoyada en cuatro servicios gestionados en la nube. No hay servidores propios de base de datos ni archivos locales persistentes: todo estado vive en Neo4j, Supabase (Postgres + Storage) y los proveedores de LLM.

```
┌────────────┐        ┌─────────────────────────────────────────────┐
│  Frontend  │  HTTP  │                Backend (FastAPI)            │
│ React+Vite ├───────►│  /api/v1: ingesta · grafo · recuperacion ·  │
│  (Vercel)  │  SSE   │           auditoria · evaluacion            │
└────────────┘        └───┬─────────┬─────────┬─────────┬───────────┘
                          │         │         │         │
                 ┌────────▼──┐ ┌────▼─────┐ ┌─▼───────┐ ┌▼──────────────┐
                 │   Neo4j   │ │ Supabase │ │Supabase │ │ OpenAI / Groq │
                 │  AuraDB   │ │ Postgres │ │ Storage │ │ CrossRef      │
                 │  (grafo)  │ │(pgvector)│ │(objetos)│ │ Unpaywall     │
                 └───────────┘ └──────────┘ └─────────┘ └───────────────┘
```

## Flujo completo del documento

```
1. CARGA        POST /ingesta/cargar → PDF a Storage → pipeline en hilo de fondo
2. EXTRACCIÓN   texto (pymupdf4llm) → referencias APA (LLM) → citas APA (regex+LLM)
                → grafo en Neo4j → vinculación cita→referencia → páginas reales (PyMuPDF)
3. REVISIÓN     humano corrige citas/referencias sobre el PDF real (cartillas editables)
4. ASOCIACIÓN   verificación por DOI (CrossRef+Unpaywall) · importación Zotero · PDF manual
                → texto de cada paper → chunks + embeddings → pgvector
5. AUDITORÍA    por cita: recuperación híbrida (grafo + vectorial) → LLM emite veredicto
                SUPPORTS / REFUTES / NO_INFO + justificación + evidencia + similitud
6. CIERRE       alertas estructurales + informe Excel descargable
```

El avance se comunica por **SSE** (`GET /ingesta/{id}/progreso`); el mismo endpoint sirve como "GET estado" porque emite el estado actual y cierra si es terminal.

## Estados del documento (`EstadoIngesta`)

```
pendiente → procesando → revision_pendiente → listo_extraccion → verificando → completado
                                                                       (error en cualquier punto)
```

- La fase 1 del pipeline termina en `revision_pendiente`; `POST /confirmar-revision` pasa a `listo_extraccion`.
- El **frontend deriva el desbloqueo de sus fases exclusivamente de este estado** (no tiene lógica de negocio propia).
- La verificación y la importación de Zotero usan `verificando` como estado transitorio y terminan en `completado`. Si la importación falla, se restaura el estado previo (nunca se bloquea el documento).

## Modelo del grafo (Neo4j)

```
(Documento)-[:TIENE_REFERENCIA]->(Referencia)-[:ESCRITO_POR]->(Autor)
(Documento)-[:TIENE_CITA]->(Cita)-[:CITA_A {confianza, metodo}]->(Referencia)
```

- La vinculación automática cita→referencia usa apellido+año (exacto 0.95, parcial 0.75); la manual fija confianza 1.0.
- Los veredictos de auditoría se persisten como propiedades del nodo `Cita` (`veredicto`, `justificacion`, `fragmento_evidencia`, `similitud`, `pagina_paper`, métricas RAGAS).
- La verificación persiste en `Referencia`: `doi_verificado`, `nivel_confianza`, `titulo_oficial`, `score_crossref`.

## Almacenes de datos

| Almacén | Qué guarda |
|---|---|
| **Neo4j** | El grafo (documentos, citas, referencias, autores) y todos los resultados de auditoría |
| **Supabase pgvector** (`papers_chunks`) | Chunks de los papers fuente con embedding 1536-d (`text-embedding-3-small`) e índice ivfflat coseno |
| **Supabase Postgres** (`auditoria_progreso`) | Estado/porcentaje del pipeline (lo consulta el SSE) |
| **Supabase Storage** (bucket privado) | Objetos por documento — ver layout |

Layout del bucket:

```
uploads/{documento_id}.pdf       PDF original
processed/{documento_id}.md      Markdown extraído (saneado de caracteres invisibles)
papers/{doi_normalizado}.txt     Cache de texto de papers externos
ubicaciones/{documento_id}.json  Cache de localización de citas (página + rects) + página de referencias
evaluacion/{documento_id}.json   Resultado Kappa/F1 vs experto
zotero/{documento_id}.json       Resumen de la última importación de Zotero
```

## Decisiones de diseño relevantes

| Decisión | Racional |
|---|---|
| **Verificación solo por DOI** | La búsqueda difusa en CrossRef por título/autor asociaba papers equivocados. Sin DOI → `no_encontrado`, y se resuelve vía Zotero o PDF manual. |
| **Importación Zotero (.ris/.zip)** | El RIS aporta DOIs y metadatos curados; el ZIP aporta además los PDFs reales (incl. de pago). Matching: DOI exacto → título normalizado ≥0.90 + primer autor + año. Los metadatos de Zotero **sobrescriben** los extraídos por el LLM. |
| **Localización de citas con PyMuPDF** | La página de cada cita se calcula con `search_for` sobre el PDF real (la extracción solo estima). El backend devuelve página + rectángulos; el frontend solo dibuja. |
| **Saneo de caracteres invisibles** | `pymupdf4llm` deja zero-width spaces (U+200B) que rompen los regex de secciones (`\s` no los matchea). Se eliminan en un único punto (`pdf_service.extraer_texto`). |
| **RAGAS ≠ evaluación vs experto** | RAGAS mide calidad interna sin ground truth; Kappa/F1 compara contra etiquetas de un experto. Son módulos, endpoints y vistas separados. Ambos viven en `/admin`, fuera del flujo del usuario. |
| **Modelo LLM centralizado** | `gpt-5.4-mini` sale de `config.py` (`OPENAI_MODEL`); si el modelo rechaza `temperature`, los clientes reintentan sin el parámetro. Embeddings fijos en `text-embedding-3-small`. |
| **Tareas largas en hilos + SSE** | Extracción, verificación e importación corren en hilos daemon y reportan a `auditoria_progreso`; el cliente sigue el avance por SSE con fallback a polling. (En Cloud Run esto exige `--no-cpu-throttling`.) |

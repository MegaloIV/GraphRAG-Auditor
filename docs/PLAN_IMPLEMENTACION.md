# GraphAuditor — Plan de Implementación (Prompt de Ejecución)

> **Naturaleza de este documento.** Es el prompt de ejecución que consolida toda la
> planificación acordada tras la auditoría del repositorio. Describe **qué construir,
> dónde, con qué firma y con qué criterio de aceptación**. Sustituye a la fase de
> planificación: a partir de aquí se implementa por bloques (A → B → C), confirmando
> avances al terminar cada bloque.
>
> **Reglas duras (heredadas del prompt original, siguen vigentes):**
> - Procesamiento **de un documento a la vez**. Nada de lotes.
> - **Modelo LLM objetivo: `gpt-5.4-mini`** en todo el sistema. Embeddings intactos en `text-embedding-3-small`.
> - **No mezclar** métricas RAGAS (calidad interna, sin ground truth) con la evaluación
>   contra ground truth experto (Kappa/F1). Son módulos y vistas **separados**.
> - El **`ground_truth_loader` es solo scaffolding comentado, sin lógica** (§B3).
> - Verificar siempre contra el código real; si el docstring/README contradice el código, gana el código.
> - No borrar datos ni credenciales. `.env.example` sin secretos.
> - Mantener estilo/convenciones/librerías del repo salvo cambio aprobado.

---

## 0. Contexto del sistema

GraphAuditor es una API de auditoría semántica de citas y referencias APA 7ª que verifica
que cada cita de una tesis sea fiel a su fuente, usando un grafo de conocimiento (Neo4j) +
búsqueda vectorial (Supabase/pgvector), veredictos por LLM y evaluación con RAGAS. Base URL
de la API: **`/api/v1`**.

Flujo:

```
PDF → Ingesta → Extracción LLM → REVISIÓN HUMANA (cartillas + PDF) → Grafo Neo4j
                                                                        ↓
                              Verificación externa (CrossRef + Unpaywall + Supabase)
                                                                        ↓
                              Recuperación híbrida (GraphRAG) → Auditoría (gpt-5.4-mini)
                                                                        ↓
                    Veredictos + RAGAS (calidad interna) + Evaluación Kappa/F1 (vs experto)
```

---

## 1. Estado actual verificado (resumen de la auditoría)

El backend está ~85 % completo y **el pipeline funciona de extremo a extremo**. Lo relevante:

- **Migración a nube ya hecha (intencional):** los archivos viven en **Supabase Storage**
  (`app/services/storage/supabase_storage_service.py`), el progreso del SSE en **Supabase
  Postgres** (`app/services/ingesta/progreso_repository.py`, tabla `auditoria_progreso`), y
  los embeddings en **Supabase pgvector** (`app/services/vectorstore/supabase_service.py`,
  tabla `papers_chunks`) — **no ChromaDB** (los docstrings que dicen "ChromaDB" son *drift*).
  El logging es solo a `stdout` (sin `logs/app.log`). No existen `UPLOAD_DIR`/`PROCESSED_DIR`.
- **Human-in-the-loop ya está casi todo implementado** en backend: CRUD de citas/referencias,
  re-vinculación y estado intermedio `revision_pendiente` en `app/api/routes/grafo.py` y
  `app/services/grafo/grafo_carga_service.py`, más `GET /ingesta/{id}/markdown` y
  `POST /ingesta/{id}/confirmar-revision`.
- **RAGAS correcto** (`app/services/evaluacion/ragas_service.py`): usa `faithfulness`,
  `answer_relevancy` y `context_utilization` (expuesto como `context_precision`), sin métricas
  que exijan ground truth. **Único defecto:** hardcodea `gpt-4o-mini`.
- **Lo que falta de verdad:** módulo de evaluación Kappa/F1 (§B2), scaffolding del loader (§B3),
  migración de modelo (§A2) y **un bug crítico** que rompe la edición de citas (§A1).

### 1.1 Endpoints existentes (confirmados en código)

- **Ingesta** (`/api/v1/ingesta`): `POST /cargar`, `GET /{id}/estructura`, `GET /{id}/progreso` (SSE),
  `POST /{id}/verificar`, `GET /{id}/markdown`, `POST /{id}/confirmar-revision`.
- **Grafo** (`/api/v1/grafo`): `GET /{id}/referencias`, `GET /{id}/citas`, `GET /{id}/resumen`,
  `GET /{id}/grafo-visual`, `PATCH|POST|DELETE /{id}/citas[/{cita_id}]`,
  `PATCH|POST|DELETE /{id}/referencias[/{ref_id}]`, `POST /{id}/re-vincular`,
  `POST /{id}/referencias/{ref_id}/paper`.
- **Recuperación** (`/api/v1/recuperacion`): `GET /{id}/motor/estado`, `POST /{id}/motor/consultar`,
  `GET /{id}/motor/cita/{cita_id}`, `GET /{id}/motor/evidencia/{cita_id}`.
- **Auditoría** (`/api/v1/auditoria`): `POST /{id}/auditar`, `GET /{id}/veredictos`, `GET /{id}/alertas`,
  `GET /{id}/alertas/alucinaciones`, `POST /{id}/evaluar-ragas`, `GET /{id}/metricas`,
  `GET /{id}/metricas/exportar`.

### 1.2 Estados del documento (enum `EstadoIngesta`)

`pendiente → procesando → revision_pendiente → listo_extraccion → verificando → completado` (+ `error`).
La Fase 1 del pipeline termina en `revision_pendiente`; `confirmar-revision` pasa a `listo_extraccion`.

---

## 2. Decisiones tomadas y defaults asumidos

| # | Tema | Decisión / Default |
|---|------|--------------------|
| D1 | `gpt-5.4-mini` + `temperature` | Centralizar el modelo en `config.py`. En las llamadas, intentar con `temperature=0.0`; **si el modelo lo rechaza, omitir el parámetro** (usar el default del modelo). Confirmar contrato real del SDK. |
| D2 | Persistencia de resultados Kappa/F1 | **JSON en Supabase Storage**: objeto `evaluacion/{documento_id}.json`. Coherente con la migración a nube. |
| D3 | Fuente del ground truth para probar §B2 ya | `POST /evaluacion/{id}/evaluar` acepta las etiquetas del experto **en el body** (`[{cita_id, etiqueta_experto}]`). El `ground_truth_loader` queda como scaffolding sin lógica y se integra después. |
| D4 | Revisión humana | Se **descarta el visor de Markdown resaltado**. Se usa **PDF real + cartillas editables**; la localización exacta (página + coordenadas) la calcula el backend con PyMuPDF `search_for`. |
| D5 | Frontend | **Rebuild desde cero** reutilizando patrones útiles del actual (sobre todo el cliente API centralizado y el manejo de `ErrorResponse`). |
| D6 | Coincidencia de nombres con/sin tilde | Ya resuelto en auto-vinculación vía `unidecode`. En los selectores manuales, aplicar búsqueda **insensible a tildes y mayúsculas**. **Nunca** se reescribe el texto original: la normalización es solo para comparar. |
| D7 | Ubicación de la evaluación Kappa/F1 | Es un **apartado de administración**, NO parte del flujo del usuario. Vive en una sección `/admin` separada, sin enlazar desde el stepper del documento. El backend (`/evaluacion`) es el mismo; solo cambia dónde se consume en el frontend. |
| D8 | Acceso al apartado admin | **Sin autenticación** (decidido). El área `/admin` es solo una **ruta separada no enlazada** desde el flujo del usuario; no se añade login/usuarios/roles al backend. Si en el futuro se requiere auth, será un bloque nuevo aparte. |
| D9 | RAGAS: ubicación y descarga | La **vista/dashboard de RAGAS** pasa al apartado **admin** (`/admin/ragas`), junto a Kappa/F1 pero como **vista separada** (regla dura de no mezclar). El **usuario NO ve el dashboard de RAGAS**, pero **sí puede descargar el informe de auditoría** (Excel `GET /auditoria/{id}/metricas/exportar`) al cierre de su flujo. RAGAS se **dispara desde admin** (`POST /evaluar-ragas`); el informe incluye las columnas RAGAS solo si ya se evaluó, si no van vacías (los veredictos siempre están). |
| D10 | Tema y sistema de estilos | **Tema claro por defecto** (estética académica/print) + **modo oscuro conmutable**; se **conserva el selector de acento** (del `accentStore` actual). Estilos con **tokens CSS propios** (variables), sin kit de componentes pesado. |
| D11 | Patrón de navegación | **Workspace de documento único** con **riel de fases vertical** y **gating por el estado real** del backend (no lógica del frontend). Tono orientado a **claridad para el jurado** (aire, textos guía, poca densidad). |

---

## 3. BLOQUE A — Correcciones (rápidas, desbloquean todo)

### A1 — 🔴 Bug crítico: `NameError: TipoCita`
- **Archivo:** `backend/app/services/grafo/grafo_carga_service.py`.
- **Problema:** la línea 7 importa `from app.models.grafo import ReferenciaAPA, CitaEnTexto, ResumenGrafo`
  pero `_leer_cita` usa `TipoCita` (≈línea 263). Cualquier `PATCH/POST /grafo/{id}/citas...`
  lanza `NameError` al construir la respuesta → **rompe editar/crear citas en la revisión**.
- **Fix:** añadir `TipoCita` al import.
- **Aceptación:** editar y crear una cita vía API retorna 200 con el `CitaEnTexto` correcto;
  el `tipo` se refleja bien.

### A2 — Migración de modelo a `gpt-5.4-mini`
- **Fuente única:** `backend/app/core/config.py:23` → `openai_model: str = "gpt-5.4-mini"`.
- **RAGAS:** `backend/app/services/evaluacion/ragas_service.py:30` → dejar de hardcodear
  `model="gpt-4o-mini"` y usar `settings.openai_model`.
- **`temperature` (D1):** encapsular la llamada para que, si el modelo rechaza `temperature`,
  se reintente sin ese parámetro. Afecta `openai_client.py`, `groq_client.py` (Groq no cambia
  de modelo) y el `ChatOpenAI` de `ragas_service.py`.
- **Docs:** actualizar `README.md:322`, `docs/BACKEND.md:106` y `docs/BACKEND.md:494`.
- **`.env`:** confirmar que `OPENAI_MODEL` apunte a `gpt-5.4-mini` (no tocar secretos).
- **Aceptación:** una llamada de extracción y una de auditoría completan sin error de parámetros;
  el modelo efectivo proviene de `config` (ninguna referencia hardcodeada a `gpt-4o-mini` en `.py`).

### A3 — Saneo de docstrings (cosmético, sin cambio funcional)
- Reemplazar menciones a "ChromaDB" por "Supabase/pgvector" y a "GPT-4o-mini" por "gpt-5.4-mini"
  en docstrings de: `auditoria_service.py`, `recuperacion_service.py`, `verificacion_service.py`,
  `embedding_service.py`.
- En `grafo_carga_service.vincular_citas`, corregir el docstring: la **estrategia 3 (solo año)
  fue eliminada**; el texto aún la describe (≈líneas 143-147) aunque el código ya no la aplica.
- **Aceptación:** ningún docstring afirma tecnologías/modelos que el código no usa.

### A4 — `backend/.env.example`
- Crear con **todas** las variables, sin secretos, incluyendo las de la migración a nube:
  `APP_ENV, APP_PORT, GROQ_API_KEY, GROQ_MODEL, OPENAI_API_KEY, OPENAI_MODEL (=gpt-5.4-mini),
  OPENAI_EMBEDDING_MODEL (=text-embedding-3-small), CROSSREF_EMAIL, NEO4J_URI, NEO4J_USERNAME,
  NEO4J_PASSWORD, NEO4J_DATABASE, SUPABASE_DB_HOST, SUPABASE_DB_PORT, SUPABASE_DB_NAME,
  SUPABASE_DB_USER, SUPABASE_DB_PASSWORD, SUPABASE_URL, SUPABASE_SERVICE_KEY,
  SUPABASE_STORAGE_BUCKET, MAX_PDF_SIZE_MB, LOG_LEVEL`.
- **Aceptación:** copiar `.env.example` a `.env` y rellenar permite arrancar sin `KeyError`.

---

## 4. BLOQUE B — Backend nuevo

### B1 — Revisión humana: PDF real + localización exacta

**Objetivo:** que la fase de revisión muestre el **PDF tal cual** y que cada cartilla (cita) lleve
al **punto exacto** del PDF donde aparece, con un recuadro de resaltado. La localización la calcula
el backend con PyMuPDF (`fitz`), **no** el frontend por matching de texto.

**B1.1 — Servir el PDF**
- **Endpoint:** `GET /api/v1/ingesta/{documento_id}/pdf` → `StreamingResponse` `application/pdf`.
- **Implementación:** `storage_service.obtener_local(f"uploads/{documento_id}.pdf")` (o
  `descargar_bytes`). 404 con `ErrorResponse` si no existe.
- **Archivo:** `backend/app/api/routes/ingesta.py`.

**B1.2 — Ubicaciones de citas (página real + coordenadas)**
- **Endpoint nuevo:** `GET /api/v1/grafo/{documento_id}/citas/ubicaciones`.
- **Respuesta (nuevo modelo `UbicacionCita` en `app/models/grafo.py`):**
  ```
  UbicacionCita:
    cita_id: str
    texto_cita: str
    pagina_real: int | None          # página 1-based del PDF (no la estimación por bloques)
    rects: list[RectResaltado]        # 0..n rectángulos donde aparece la cita
  RectResaltado:
    pagina: int                       # 1-based
    x0: float; y0: float; x1: float; y1: float
    ancho_pagina: float; alto_pagina: float   # dimensiones del page para escalar en el frontend
  ```
- **Servicio nuevo:** `app/services/ingesta/localizacion_service.py`:
  - Abre el PDF con `fitz.open(ruta_local)`.
  - Para cada cita del documento (leídas de Neo4j), busca en cada página con
    `page.search_for(texto_cita)`. Si no hay match, cae a variantes construidas del texto:
    `"Apellido (año)"`, `"Apellido, año"`, `"(Apellido, año)"` (derivadas del `texto_cita`).
  - Toma el primer match → `pagina_real` (1-based) + `rects`. Sin match → `pagina_real=None`, `rects=[]`.
  - Devuelve dimensiones de página (`page.rect.width/height`) por rectángulo.
- **Rendimiento:** ~decenas de citas → aceptable en una pasada. Cachear el resultado por
  `documento_id` (p. ej. objeto `ubicaciones/{documento_id}.json` en Storage) e invalidar cuando
  se editen/creen/eliminen citas.
- **Aceptación:** para una tesis real, ≥ la mayoría de las citas devuelven `pagina_real` y al menos
  un rectángulo; las no localizadas quedan con `pagina_real=None` (la cartilla igual es editable).

> **Nota de alcance honesta:** el recuadro sobre `texto_cita` es fiable. Resaltar la oración
> completa (`fragmento_oracion`) es "mejor esfuerzo": si `search_for` la encuentra en una sola
> página, se resalta; si es prosa larga multi-línea, se muestra completa en la cartilla y basta
> con anclar la cita. No bloquear la fase por esto.

### B2 — Módulo de evaluación contra ground truth (Kappa / F1 / matriz de confusión)

**Objetivo:** medir el desempeño del sistema comparando el `veredicto` de cada `Cita`
(SUPPORTS/REFUTES/NO_INFO en Neo4j) contra las etiquetas de un experto. **Separado de RAGAS.**

**B2.1 — Servicio de métricas**
- **Archivo:** `backend/app/services/evaluacion/metricas_service.py`.
- **Dependencia:** `scikit-learn==1.8.0` (**ya está** en `requirements.txt`).
- **Función principal:**
  ```
  calcular_metricas(pares: list[tuple[str, str]]) -> dict
      # pares = [(prediccion_sistema, etiqueta_experto), ...] con labels en
      # {"SUPPORTS","REFUTES","NO_INFO"}
  ```
  Devuelve:
  - **Matriz de confusión 3×3** con orden fijo de labels `["SUPPORTS","REFUTES","NO_INFO"]`
    (`sklearn.metrics.confusion_matrix(..., labels=LABELS)`).
  - **Precisión, recall y F1 por categoría** + agregados **macro** y **weighted**
    (`precision_recall_fscore_support` con `average=None`, `"macro"`, `"weighted"`; `zero_division=0`).
  - **Kappa de Cohen** (`cohen_kappa_score`).
  - Conteos: total evaluadas, aciertos (traza de la matriz), desacuerdos por celda.
- **Casos borde:** citas del sistema sin etiqueta experta (y viceversa) → **reportar como
  "no emparejadas", no romper**; excluirlas del cálculo y contarlas aparte.

**B2.2 — Modelos** (`backend/app/models/evaluacion.py`, nuevos):
```
CategoriaMetricas:      { categoria, precision, recall, f1, soporte }
MatrizConfusion:        { labels: list[str], filas: list[list[int]] }   # filas = verdadero(experto), columnas = predicho(sistema)
EvaluacionResultado:
    documento_id: str
    total_evaluadas: int
    aciertos: int
    kappa_cohen: float
    matriz_confusion: MatrizConfusion
    por_categoria: list[CategoriaMetricas]
    macro: CategoriaMetricas
    weighted: CategoriaMetricas
    no_emparejadas_sistema: int
    no_emparejadas_experto: int
    evaluado_en: str            # ISO 8601
EtiquetaExperto:        { cita_id: str, etiqueta_experto: VeredictoTipo }
EvaluarRequest:         { etiquetas: list[EtiquetaExperto] }   # fuente temporal del ground truth (D3)
```

**B2.3 — Router** `backend/app/api/routes/evaluacion.py`, `prefix="/evaluacion"`, registrado en `main.py`:
- `POST /{documento_id}/evaluar` — lee los `veredicto` de las citas del doc en Neo4j, empareja por
  `cita_id` con `EvaluarRequest.etiquetas`, llama a `metricas_service`, **persiste** el
  `EvaluacionResultado` (D2: `evaluacion/{documento_id}.json` en Storage) y lo retorna.
  - `// TODO(B3): sustituir el body por ground_truth_loader.cargar(documento_id) cuando se defina.`
- `GET /{documento_id}/resultados` — devuelve el `EvaluacionResultado` persistido (404 si no hay).
- `GET /{documento_id}/exportar` — exporta a **Excel** (openpyxl, como el de RAGAS) y/o JSON:
  hoja "Resumen" (Kappa, macro/weighted, conteos) + hoja "Matriz".
- **Aceptación:** con un set de etiquetas de prueba, `POST evaluar` retorna matriz + P/R/F1 (por
  categoría, macro y weighted) + Kappa coherentes; `resultados` y `exportar` funcionan.

### B3 — `ground_truth_loader` (SOLO SCAFFOLDING, SIN LÓGICA)
- **Archivo:** `backend/app/services/evaluacion/ground_truth_loader.py`.
- Contiene **únicamente**:
  - Docstring de módulo explicando el propósito.
  - Opciones **comentadas** (sin código funcional): **A)** CSV (`cita_id, etiqueta_experto`),
    **B)** Excel `.xlsx`, **C)** JSON, **D)** clasificación dentro del sistema (UI del experto).
  - Firmas con `raise NotImplementedError` y `# TODO: definir mecanismo`, documentando entradas/salidas
    esperadas de cada opción (todas deben devolver `list[EtiquetaExperto]` para `documento_id`).
  - **Ninguna** lógica de parseo, lectura de archivos ni BD.
- En `metricas_service`/router dejar el comentario del **punto de integración futuro** (ver B2.3).

### B4 — (Opcional) Esquema versionado de Supabase
- `backend/db/supabase_schema.sql` con el `CREATE TABLE papers_chunks (...)` (incluida
  `embedding vector(1536)`), el índice ANN **ivfflat** coseno, y la tabla `auditoria_progreso`.
  Solo documentación reproducible; no cambia comportamiento.

---

## 5. BLOQUE C — Frontend nuevo (React + Vite)

**Stack confirmado:** Node **v20.20.2**, React 19, Vite 8, `react-router-dom` 7, `axios`,
`react-dropzone`, `lucide-react`; grafo con **`react-force-graph-2d`** (ya instalado).
**Añadir:** una librería de PDF (**`react-pdf`** / pdf.js) para el visor de la revisión.
CORS del backend ya admite `http://localhost:5173`.

### 5.0 Diseño visual y de navegación

**Tema y estilo (D10):**
- **Tema claro por defecto**, estética académica/limpia (transmite confianza; se lee como documento).
  Tipografía **sans-serif para la UI** y **serif para el texto del documento/paper**.
- **Modo oscuro conmutable** (barato con tokens) + **selector de acento** conservado del `accentStore`.
- **Estilos con tokens CSS propios** (variables): `--accent`, `--bg`, `--bg-surface`, `--text`,
  `--text-secondary`, `--border`, `--radius-*`, `--shadow-*`, y colores de veredicto. Sin kit pesado.
- **Código de color de veredictos, único en toda la app:** SUPPORTS = **verde**, REFUTES = **rojo**,
  NO_INFO = **gris**, advertencias/alertas = **ámbar**.

**Navegación (D11): workspace de documento único con riel de fases + gating por estado.**

```
┌───────────────────────────────────────────────────────────────────────┐
│ GraphAuditor   📄 tesis_juan_perez.pdf      ● backend ok   🌗   ⊕ nuevo │  top bar
├──────────────┬────────────────────────────────────────────────────────┤
│ ① Carga     ✓ │                                                        │
│ ② Revisión  ● │            [ CONTENIDO DE LA FASE ACTIVA ]             │
│ ③ Verificar 🔒│                                                        │
│ ④ Grafo     🔒 │                                                       │
│ ⑤ Auditoría 🔒 │                                                       │
│ ⑥ Cierre    🔒 │                                                       │
├──────────────┴────────────────────────────────────────────────────────┤
│                          [ Continuar → ]                               │
└───────────────────────────────────────────────────────────────────────┘
```

- **Riel de fases del usuario (6):** ① Carga · ② Revisión · ③ Verificación · ④ Grafo · ⑤ Auditoría ·
  ⑥ Cierre. Estados por fase: **✓ completa · ● activa · 🔒 bloqueada**. El desbloqueo depende del
  `estado` real que reporta el backend (progreso/SSE), **no** de lógica del frontend.
- **Top bar:** marca, documento activo, **salud del backend** (`GET /health`), toggle de tema,
  botón "nuevo documento".
- **Tono:** claridad para jurado — aire, textos guía cortos, jerarquía visual clara, pocas cosas por
  pantalla.
- **Responsive:** en móvil el riel colapsa a stepper horizontal/menú; los layouts de dos paneles
  (revisión PDF + cartillas) se apilan verticalmente.

**Fase ⑥ Cierre (resumen read-only, pensada para el jurado):** conteo de veredictos con sus colores,
cobertura de verificación, densidad del grafo y alertas, más un botón grande **Descargar informe**
(`GET /auditoria/{id}/metricas/exportar`). Da sensación de "trabajo terminado".

**Home:** dropzone central de carga + **documentos recientes** (persistidos en `localStorage`, ya que
el backend opera por `documento_id`); se persiste el documento activo para sobrevivir recargas.

**Apartado admin:** rutas `/admin/ragas` y `/admin/evaluacion`, **sin login** (D8), no enlazadas desde
el flujo del usuario, con un **selector de documento** compartido en la cabecera.

**Componentes clave reutilizables:** `BadgeVeredicto` (único; define color+ícono), `CartillaCita`,
`CartillaReferencia`, `SelectorReferencia` (búsqueda sin tildes — D6), `VisorPDF`, `TarjetaMetrica`,
`BarraProgresoSSE`, `RielFases`, `TopBar`, `EstadoVacio/Carga/Error`.

**Robustez:** hook `useSSEProgreso` con **reintento/reconexión** y *fallback* a *polling* de
`GET /progreso` (para que la barra no se congele si cae la red); estados de carga/vacío/error usando
siempre el `accion_sugerida` del `ErrorResponse`.

### 5.1 Estructura de carpetas
```
src/
  api/            # cliente axios centralizado (VITE_API_BASE_URL) + módulos por dominio
  pages/          # una por ruta (incluye pages/admin/ para el apartado de administración — D7)
  components/     # reutilizables (badges de veredicto, cartillas, tarjetas de métricas, barra SSE, visor PDF)
  hooks/          # useSSEProgreso, useDocumento, etc.
  lib/            # utils (normalización sin tildes para búsquedas, formateo)
  store/          # estado ligero (documento activo, tema)
```
- **Cliente API central:** una sola base URL por env (`VITE_API_BASE_URL`, default
  `http://localhost:8000/api/v1`) + interceptor que traduce el `detail` (`ErrorResponse`:
  `codigo/mensaje/accion_sugerida`) — reutilizar el patrón del `src/api/client.js` actual.
- **Tipado / shape** consistente con los modelos del backend.

### 5.2 Rutas / pantallas

| Ruta | Pantalla | Endpoints |
|------|----------|-----------|
| `/` | Carga de PDF (respeta `MAX_PDF_SIZE_MB`) + indicador de salud backend | `POST /ingesta/cargar`, `GET /health` |
| `/doc/:id/progreso` | Barra de progreso en tiempo real (SSE) con porcentaje y mensaje | `GET /ingesta/{id}/progreso` |
| `/doc/:id/revision` | **Revisión humana (ver §5.3)** | PDF + citas/refs CRUD + re-vincular + confirmar |
| `/doc/:id/verificar` | Selección de referencias a verificar + subir PDF manual de refs no encontradas | `POST /ingesta/{id}/verificar`, `POST .../paper` |
| `/doc/:id/grafo` | Grafo interactivo + detalle de nodo (confianza, DOI, veredicto, similitud) | `GET /grafo/{id}/grafo-visual` |
| `/doc/:id/auditoria` | Lanzar auditoría; por cita: **badge** SUPPORTS/REFUTES/NO_INFO, justificación, fragmento de evidencia, similitud | `POST /auditar`, `GET /veredictos`, `recuperacion/*` |
| `/doc/:id/alertas` | **Cierre del flujo:** alertas (citas sin referencia + referencias sin citar + alucinaciones NO_INFO), resumen del documento y **descarga del informe** (Excel de auditoría) | `GET /alertas`, `GET /alertas/alucinaciones`, `GET /auditoria/{id}/metricas/exportar` |

> El **usuario NO tiene** pantalla de RAGAS (movida a admin — D9), pero **sí descarga el informe**
> de auditoría en la fase de cierre.

**Apartado de administración (fuera del flujo del usuario — D7/D8):**

| Ruta | Pantalla | Endpoints |
|------|----------|-----------|
| `/admin/ragas` | **RAGAS** (calidad interna del sistema): selector de documento, promedios + por cita (faithfulness, answer_relevancy, context_precision), botón para lanzar la evaluación | `POST /auditoria/{id}/evaluar-ragas`, `GET /auditoria/{id}/metricas`, `GET /auditoria/{id}/metricas/exportar` |
| `/admin/evaluacion` | **Kappa/F1** (desempeño vs experto): selector de documento, matriz de confusión, P/R/F1 por categoría + macro/weighted, Kappa con interpretación legible | `POST /evaluacion/{id}/evaluar`, `GET /evaluacion/{id}/resultados`, `GET /evaluacion/{id}/exportar` |

> **RAGAS** (calidad interna) y **Evaluación Kappa/F1** (vs experto) viven **ambos en el apartado
> admin**, pero como **vistas separadas** (`/admin/ragas` y `/admin/evaluacion`) — regla dura de no
> mezclarlos. El admin **no lleva autenticación** (D8): rutas aparte no enlazadas desde el flujo del
> usuario. Exportación Excel disponible en ambas vistas admin.

### 5.3 Pantalla de revisión (la clave — reemplaza el visor roto)

**Layout de dos paneles:**

- **Panel izquierdo — Visor PDF real** (`react-pdf`): muestra el PDF servido por
  `GET /ingesta/{id}/pdf`. Al seleccionar una cartilla, **salta a `pagina_real`** y dibuja un
  **overlay de resaltado** con los `rects` (escalados con `ancho_pagina/alto_pagina`) de
  `GET /grafo/{id}/citas/ubicaciones`. Resaltado que parpadea ~2 s al enfocar.
- **Panel derecho — Cartillas** (pestañas **Citas** / **Referencias**): lista scrolleable de
  tarjetas, una por elemento.

**Cartilla de CITA** (campos editables, guardado directo):
- Cabecera: badge `tipo` (Parentética/Narrativa), `página`, estado de vínculo
  (`vinculada a …` / ⚠️ `sin referencia`).
- `texto_cita` (input, copiar/pegar y corregir).
- `fragmento_oracion` = **aseveración del tesista** (textarea; el usuario aprueba o pega el texto correcto).
- **Referencia vinculada:** selector con **buscador insensible a tildes/mayúsculas** (D6) sobre la
  lista de referencias del documento; opción "sin vincular".
- `pagina` (número).
- Acciones: **Guardar** (`PATCH /grafo/{id}/citas/{cita_id}`; el vínculo usa el mismo `PATCH` con
  `referencia_id`), **Eliminar** (`DELETE`). Indicador de "sin guardar", confirmación de guardado y
  error inline desde `ErrorResponse`.
- Comportamiento: clic en la cartilla → el visor salta y resalta el punto exacto.

**Cartilla de REFERENCIA:** `autores` (chips editables), `anio`, `titulo`, `fuente`, `doi`,
toggle `datos_incompletos`. Guardar `PATCH /grafo/{id}/referencias/{ref_id}`, eliminar `DELETE`.

**Cabecera de la lista:** contadores (`N citas · M sin referencia · K manuales`), filtro
"mostrar solo sin vincular", botón **"+ Añadir cita manual"** (`POST`), botón **"Re-vincular
automáticamente"** (`POST /re-vincular`), y **"Confirmar datos y continuar"**
(`POST /ingesta/{id}/confirmar-revision`, habilita la verificación).

**Búsqueda insensible a tildes (D6):** util en `src/lib/` que normaliza query y etiqueta con
`str.normalize('NFD')` + strip de diacríticos + minúsculas antes de comparar (misma técnica que
`quitarAcentos` del `resaltador.js` actual). **No** se altera el texto mostrado ni el guardado.

### 5.4 Principios de UI (obligatorios)
- Simple, claro e intuitivo para un jurado no técnico; responsive (móvil/tablet/escritorio).
- Estados de carga, vacío y error bien manejados (spinners, mensajes con `accion_sugerida`).
- Feedback inmediato en cada edición.
- Componentes reutilizables: `BadgeVeredicto`, `CartillaCita`, `CartillaReferencia`,
  `TarjetaMetrica`, `BarraProgresoSSE`, `VisorPDF`, `SelectorReferencia`.

---

## 6. Prioridades, dependencias y complejidad

| Orden | Ítem | Depende de | Complejidad |
|------|------|-----------|-------------|
| 1 | A1 fix `TipoCita` | — | Baja |
| 2 | A2 migración `gpt-5.4-mini` (+D1) | — | Baja |
| 3 | A3 docstrings · A4 `.env.example` | — | Baja |
| 4 | B1.1 `GET /ingesta/{id}/pdf` | — | Baja |
| 5 | B1.2 ubicaciones (search_for) | B1.1 | Media |
| 6 | B2 módulo Kappa/F1 (+D2, D3) | A1 (veredictos íntegros) | Media |
| 7 | B3 `ground_truth_loader` (scaffolding) | B2 | Baja |
| 8 | B4 `supabase_schema.sql` (opcional) | — | Baja |
| 9 | C1 andamiaje frontend + cliente API | A1–A2 | Media |
| 10 | C revisión (PDF + cartillas) | B1, A1 | Alta |
| 11 | C grafo · auditoría · RAGAS | C1 | Alta |
| 12 | C evaluación Kappa/F1 · alertas · exportaciones | B2, C1 | Media |

---

## 7. Definición de "hecho" (aceptación global)

1. Editar/crear/eliminar/re-vincular citas y referencias funciona vía API y desde la UI (sin `NameError`).
2. Ninguna referencia hardcodeada a `gpt-4o-mini` en `.py`; el modelo efectivo sale de `config`
   y las llamadas no fallan por `temperature`.
3. En la revisión, el PDF real se muestra y cada cartilla salta y resalta el punto exacto de su cita;
   las citas no localizadas siguen siendo editables.
4. El módulo de evaluación devuelve matriz 3×3, P/R/F1 (categoría + macro + weighted) y Kappa,
   maneja citas no emparejadas sin romper, y persiste/exporta el resultado.
5. `ground_truth_loader` existe solo como scaffolding comentado (sin lógica).
6. RAGAS y Evaluación Kappa permanecen separados en backend y frontend.
7. `.env.example` completo permite arrancar; docs sin afirmaciones falsas (ChromaDB/modelo/estrategia-3).

---

## 8. Decisiones que aún puedes cambiar (si no, se aplican los defaults de §2)

- **D1** contrato exacto de `gpt-5.4-mini` (¿acepta `temperature`? ¿`max_completion_tokens`?).
- **D2** persistencia Kappa/F1 (Storage JSON vs Neo4j vs Postgres).
- **D3** fuente del ground truth para probar §B2 (body inline vs esperar al loader).
- Cualquier ajuste de pantallas del frontend (auditoría, RAGAS, evaluación) al estilo de lo que
  hicimos con la revisión.
```

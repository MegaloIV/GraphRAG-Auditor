# Frontend — Documentación técnica

**GraphRAG Auditor** · React 19 · Vite 8 · SPA sin router externo

Interfaz para cargar tesis en PDF y revisar los resultados de la auditoría semántica de citas APA 7ma edición. Usa SSE para seguir el progreso en tiempo real y ForceGraph2D para visualizar el grafo de conocimiento.

---

## Índice

1. [Stack tecnológico](#1-stack-tecnológico)
2. [Estructura de directorios](#2-estructura-de-directorios)
3. [Configuración y variables de entorno](#3-configuración-y-variables-de-entorno)
4. [Punto de entrada y navegación](#4-punto-de-entrada-y-navegación)
5. [Capa API (`src/api/client.js`)](#5-capa-api-srcapiclientjs)
6. [Store de estado global (`src/store/accentStore.js`)](#6-store-de-estado-global-srcstoreaccentstorejs)
7. [Páginas](#7-páginas)
8. [Componentes](#8-componentes)
9. [Sistema de diseño](#9-sistema-de-diseño)
10. [Flujo completo de usuario](#10-flujo-completo-de-usuario)

---

## 1. Stack tecnológico

| Paquete | Versión | Rol |
|---|---|---|
| `react` | 19.x | UI library |
| `react-dom` | 19.x | Renderizado DOM |
| `vite` | 8.x | Bundler y dev server |
| `axios` | 1.x | Cliente HTTP con interceptores |
| `react-dropzone` | 15.x | Zona de arrastre de archivos |
| `react-force-graph-2d` | 1.x | Visualización de grafos en canvas |
| `lucide-react` | 1.x | Iconos SVG |
| `react-router-dom` | 7.x | Instalado, pero la navegación se maneja con estado React |

**Dev:**
- `@vitejs/plugin-react` — transpilación JSX
- `eslint` 10 + plugins `react-hooks` y `react-refresh`

---

## 2. Estructura de directorios

```
frontend/
├── index.html
├── vite.config.js
├── package.json
├── eslint.config.js
├── public/
│   ├── favicon.svg
│   └── icons.svg
└── src/
    ├── main.jsx               # Punto de entrada React
    ├── App.jsx                # Router de estado (inicio ↔ auditoria)
    ├── index.css              # Variables CSS globales y estilos base
    ├── App.css
    ├── api/
    │   └── client.js          # Instancia axios + APIs por dominio
    ├── store/
    │   └── accentStore.js     # Color de acento (localStorage)
    ├── pages/
    │   ├── PaginaInicio.jsx   # Subida de PDF
    │   └── PaginaAuditoria.jsx # Panel de auditoría con tabs
    └── components/
        ├── ui/
        │   ├── Navbar.jsx
        │   ├── Card.jsx
        │   ├── Badge.jsx
        │   └── BarraProgreso.jsx
        ├── ingesta/
        │   ├── ZonaCarga.jsx
        │   ├── ProgresoAuditoria.jsx
        │   └── EstructuraDocumento.jsx
        ├── citas/
        │   └── CitasVerificacion.jsx
        ├── grafo/
        │   ├── GrafoVisual.jsx
        │   ├── ListaReferencias.jsx
        │   ├── ListaCitas.jsx
        │   └── ResumenGrafo.jsx
        ├── recuperacion/
        │   └── EstadoMotor.jsx
        └── auditoria/
            ├── ListaVeredictos.jsx
            ├── AlertasInconsistencias.jsx
            ├── AlertasAlucinaciones.jsx
            └── MetricasRagas.jsx
```

---

## 3. Configuración y variables de entorno

Archivo: `frontend/.env` (o `.env.local` para sobreescribir)

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` | URL base del backend |

El fallback está hardcodeado en `client.js` y en `PaginaAuditoria.jsx` (para el SSE).

```bash
# Desarrollo
npm run dev      # vite dev server en http://localhost:5173

# Producción
npm run build    # genera dist/
npm run preview  # sirve dist/ localmente
```

---

## 4. Punto de entrada y navegación

### `src/main.jsx`

Monta `<App />` en el elemento `#root` con `ReactDOM.createRoot`.

### `src/App.jsx`

Maneja la navegación mediante estado React (no usa React Router):

```
pagina === 'inicio'    → <PaginaInicio onDocumentoCargado={irAuditoria} />
pagina === 'auditoria' → <PaginaAuditoria documentoId={...} onVolver={irInicio} />
```

Al montar, llama a `cargarAccent()` para restaurar el color de acento desde `localStorage`.

**Flujo:**
1. Usuario sube PDF → `irAuditoria(documentoId)` cambia el estado a `'auditoria'`.
2. Usuario pulsa "Volver" → `irInicio()` limpia `documentoId` y regresa a `'inicio'`.

---

## 5. Capa API (`src/api/client.js`)

Instancia de Axios con:
- `baseURL`: `VITE_API_BASE_URL` o `http://localhost:8000/api/v1`
- `timeout`: 300 000 ms (5 min) por defecto; auditoría usa 600 000 ms (10 min)

**Interceptor de errores:** normaliza las respuestas de error del backend al formato `{codigo, mensaje, accion}`.

### Grupos de funciones

#### `ingestaAPI`

| Función | Método | Endpoint |
|---|---|---|
| `cargarPDF(archivo)` | `POST` | `/ingesta/cargar` — multipart/form-data |
| `verEstructura(documentoId)` | `GET` | `/ingesta/{id}/estructura` |
| `verProgreso(documentoId)` | `GET` | `/ingesta/{id}/progreso` |
| `iniciarVerificacion(documentoId, referenciaIds)` | `POST` | `/ingesta/{id}/verificar` |

#### `grafoAPI`

| Función | Método | Endpoint |
|---|---|---|
| `verReferencias(documentoId)` | `GET` | `/grafo/{id}/referencias` |
| `verCitas(documentoId)` | `GET` | `/grafo/{id}/citas` |
| `verGrafoVisual(documentoId)` | `GET` | `/grafo/{id}/grafo-visual` |
| `subirPaperManual(documentoId, referenciaId, archivo)` | `POST` | `/grafo/{id}/referencias/{refId}/paper` · timeout 120 s |

#### `recuperacionAPI`

| Función | Método | Endpoint |
|---|---|---|
| `estadoMotor(documentoId)` | `GET` | `/recuperacion/{id}/motor/estado` |
| `consultarCita(documentoId, citaId)` | `GET` | `/recuperacion/{id}/motor/cita/{citaId}` |
| `consultarTextoLibre(documentoId, texto, nResultados)` | `POST` | `/recuperacion/{id}/motor/consultar` |

#### `auditoriaAPI`

Todas las llamadas usan `AUDITORIA_TIMEOUT = 600 000 ms` (10 min).

| Función | Método | Endpoint |
|---|---|---|
| `auditar(documentoId)` | `POST` | `/auditoria/{id}/auditar` |
| `verVeredictos(documentoId)` | `GET` | `/auditoria/{id}/veredictos` |
| `verAlertas(documentoId)` | `GET` | `/auditoria/{id}/alertas` |
| `verAlertasAlucinaciones(documentoId)` | `GET` | `/auditoria/{id}/alertas/alucinaciones` |
| `evaluarRagas(documentoId)` | `POST` | `/auditoria/{id}/evaluar-ragas` |
| `verMetricas(documentoId)` | `GET` | `/auditoria/{id}/metricas` |
| `exportarMetricasExcel(documentoId)` | `GET` | `/auditoria/{id}/metricas/exportar` · `responseType: 'blob'` |

---

## 6. Store de estado global (`src/store/accentStore.js`)

Estado global mínimo sin librería externa: color de acento persistido en `localStorage`.

**5 opciones de color:**

| Nombre | Hex |
|---|---|
| Azul | `#3B82F6` |
| Esmeralda | `#10B981` |
| Violeta | `#8B5CF6` |
| Naranja | `#F59E0B` |
| Rosa | `#EC4899` |

**Funciones exportadas:**

| Función | Descripción |
|---|---|
| `cargarAccent()` | Lee `localStorage['graphrag-accent']` y aplica el color. Se llama al montar `App`. |
| `cambiarAccent(valor)` | Guarda en `localStorage` y aplica las variables CSS. |

**Variables CSS que actualiza:**
- `--accent`: color principal
- `--accent-hover`: variante más oscura
- `--accent-subtle`: `color + '1F'` (12% de opacidad en hex)

---

## 7. Páginas

### `PaginaInicio.jsx`

Página de bienvenida con zona de carga de PDF.

**Estado local:**
- `estado`: `'idle' | 'cargando' | 'error'`
- `error`: objeto `{mensaje, accion}` del interceptor axios

**Comportamiento:**
1. `useDropzone` acepta solo `application/pdf`, máx. 1 archivo.
2. Al soltar/seleccionar: llama a `ingestaAPI.cargarPDF()`.
3. En éxito: navega a auditoría via `onDocumentoCargado(documento_id)`.
4. En error: muestra mensaje y acción sugerida con botón "Intentar de nuevo".

**UI:**
- Hero con badge de tecnología y título animado con `fadeIn`.
- Zona de arrastre con estados visuales (idle / drag / cargando / error).
- Grid 3 columnas con chips informativos: CrossRef, GraphRAG, Neo4j.

---

### `PaginaAuditoria.jsx`

Panel principal de auditoría con sistema de tabs. Orquesta todo el flujo post-carga.

**Estado local:**

| Estado | Descripción |
|---|---|
| `progreso` | Datos del SSE: estado, porcentaje, mensaje |
| `fase` | `'extraccion'` o `'verificacion'` |
| `referencias` | Datos de `grafoAPI.verReferencias()` |
| `citas` | Datos de `grafoAPI.verCitas()` |
| `grafoVisual` | Nodos y enlaces de `grafoAPI.verGrafoVisual()` |
| `estadoMotor` | Estado del motor de recuperación |
| `auditando` | Boolean — auditoría en curso |
| `auditoriaData` | Veredictos calculados |
| `alertas` | Inconsistencias estructurales |
| `alertasAlucinacion` | Citas no verificables |
| `metricasRagas` | Promedios RAGAS del documento |

**Tabs disponibles:**

| Tab | Id | Habilitada cuando |
|---|---|---|
| Citas | `citas` | Siempre (tras fase 1) |
| Referencias | `referencias` | Fase 1 completada |
| Grafo | `grafo` | Fase 1 completada |
| Motor | `motor` | Fase 2 completada |
| Veredictos | `veredictos` | Auditoría completada |
| Alertas | `alertas` | Auditoría completada |
| Informe | `ragas` | Auditoría completada |

**SSE (Server-Sent Events):**

Conecta a `/api/v1/ingesta/{doc_id}/progreso` con `EventSource`. Reacciona a:
- `listo_extraccion` → cierra SSE, carga datos fase 1, activa tab "citas".
- `completado` → cierra SSE, carga datos fase 2 (referencias, citas, motor, veredictos, alertas, métricas RAGAS).
- `error` → cierra SSE, muestra error.

Al iniciar verificación (`handleVerificacionIniciada`), reconecta el SSE para fase 2.

**Carga de datos:**

- `cargarDatosFase1()`: `Promise.all` de referencias, citas y grafo visual.
- `cargarDatosFase2()`: referencias, citas, motor, grafo visual, veredictos, alertas, alucinaciones y métricas RAGAS (los últimos grupos en bloques try/catch independientes para no fallar si aún no existen).

---

## 8. Componentes

### `components/ui/Navbar.jsx`

Barra de navegación sticky (z-index 100).

**Props:**
- `onVolver`: callback para volver a la página de inicio.
- `mostrarVolver` (bool): muestra el botón ← de retorno.

Contiene el selector de acento: círculos de colores que llaman a `cambiarAccent()`. El color activo se escala visualmente (`scale(1.2)`) y tiene borde blanco.

---

### `components/ui/Card.jsx`

Contenedor con cabecera opcional. Props: `titulo`, `subtitulo`, `icono`, `children`, `style`.

---

### `components/ui/Badge.jsx`

Etiqueta de estado con variantes de color: `success`, `error`, `warning`, `neutral`.

---

### `components/ui/BarraProgreso.jsx`

Barra de progreso animada. Props: `porcentaje` (0–100).

---

### `components/ingesta/ProgresoAuditoria.jsx`

Muestra el progreso del pipeline en tiempo real. Recibe el objeto `progreso` del SSE.

---

### `components/citas/CitasVerificacion.jsx`

Hub central de la fase 1. Permite seleccionar referencias para verificar.

**Props:**
- `documentoId`
- `citas`: lista de `CitaEnTexto`
- `referencias`: lista de `ReferenciaAPA`
- `estadoPipeline`: estado actual del SSE
- `onVerificacionIniciada`: callback tras iniciar verificación

**Lógica de estado por cita:**

| Estado | Condición |
|---|---|
| `sin_referencia` | `cita.referencia_id` es null |
| `no_encontrado` | `ref.nivel_confianza === 'no_encontrado'` |
| `encontrado` | `ref.nivel_confianza` tiene valor válido |
| `pendiente` | Tiene referencia pero sin `nivel_confianza` |

**Selección para verificación:**

- Solo las referencias con estado `pendiente` son seleccionables.
- Por defecto, todas las pendientes están preseleccionadas.
- Botones "Todas" / "Ninguna" para selección rápida.
- El botón "Verificar N referencia(s)" llama a `ingestaAPI.iniciarVerificacion()`.

**Orden de visualización:** pendiente → no_encontrado → encontrado → sin_referencia.

**Iconos de estado:**
- `pendiente`: checkbox marcable / spinner durante verificación.
- `encontrado`: círculo verde ✓.
- `no_encontrado`: círculo rojo ✕.
- `sin_referencia`: círculo gris vacío.

---

### `components/grafo/GrafoVisual.jsx`

Visualización interactiva del grafo usando `ForceGraph2D` (canvas HTML5).

**Nodos por tipo:**

| Tipo | Color | Radio |
|---|---|---|
| Documento | Azul `#3b82f6` | 11 |
| Referencia | Variable por `nivel_confianza` | 8 |
| Cita | Variable por `veredicto` (ámbar si no auditada) | Variable por `similitud` (3–10) |
| Autor | Violeta `#a855f7` | 4 |

**Colores de Referencia por `nivel_confianza`:**

| Nivel | Color |
|---|---|
| `texto_completo` | Verde `#10b981` |
| `abstract` / `cache` | Cyan `#06b6d4` |
| `manual` | Violeta `#8b5cf6` |
| `no_encontrado` | Rojo `#ef4444` |
| sin valor | Gris `#6b7280` |

**Colores de Cita por `veredicto`** (`colorNodo`):

| Veredicto | Color |
|---|---|
| `SUPPORTS` | Verde `#10b981` |
| `REFUTES` | Rojo `#ef4444` |
| `NO_INFO` / sin auditar | Ámbar `#f59e0b` |

**Tamaño de Cita por similitud** (`radioNodo`): el radio se interpola entre `RADIO_CITA_MIN` (3) y `RADIO_CITA_MAX` (10) según la `similitud` (0–1) del fragmento recuperado por el RAG. Citas más grandes = mejor evidencia encontrada. Sin valor de similitud usa el radio base (5).

**Controles:**
- Toggle "Referencias": muestra/oculta nodos Referencia y sus enlaces (on por defecto).
- Toggle "Citas": muestra/oculta nodos Cita y sus enlaces (on por defecto).
- Toggle "Autores": muestra/oculta nodos Autor y sus enlaces (off por defecto).
- Filtro de veredicto (`FILTROS_VEREDICTO`): segmentado **Todos / SUPPORTS / REFUTES / NO_INFO / Sin auditar** que filtra los nodos Cita por su `veredicto` (`sin_auditar` = citas sin veredicto). Solo se muestra cuando el toggle "Citas" está activo.

El filtrado vive en el `useMemo` de `graphData`: descarta nodos según toggles y veredicto, y luego poda los enlaces cuyo origen o destino ya no es visible.

**Interacción:**
- Click en nodo → abre panel de detalle (`DetalleNodo`) con propiedades del nodo:
  - **Referencia**: label, título, autores, año, fuente, DOI, cobertura, score CrossRef.
  - **Cita**: texto, tipo, página, badge de veredicto, **afirmación del tesista** (`fragmento`) y **contenido original del paper** (`fragmento_evidencia`, con nº de página) como bloques de texto (`BloqueTexto`), motivo (justificación) y métricas (similitud RAG + RAGAS) como chips.
  - **Documento** / **Autor**: nombre.
- Etiquetas: siempre visibles en Documento; en otros nodos solo con zoom > 1.5.
- Altura fija: 580 px. Mide el ancho del contenedor con `ResizeObserver`.

---

### `components/grafo/ListaReferencias.jsx`

Lista de referencias bibliográficas detectadas. Muestra `nivel_confianza`, DOI y permite subir PDF manualmente.

**Subida manual de paper:**

Para referencias con `nivel_confianza === 'no_encontrado'`, ofrece un input de archivo que llama a `grafoAPI.subirPaperManual()`. Tras la subida exitosa, llama a `onRecargarReferencias()` para actualizar la lista.

---

### `components/recuperacion/EstadoMotor.jsx`

Muestra el estado de preparación del motor de búsqueda semántica y el botón para lanzar la auditoría.

**Props:**
- `datos`: respuesta de `recuperacionAPI.estadoMotor()`
- `onAuditar`: callback que llama a `auditoriaAPI.auditar()`
- `auditando` (bool): deshabilita el botón durante la auditoría

---

### `components/auditoria/ListaVeredictos.jsx`

Lista de veredictos por cita con filtros y panel de detalle expandible.

**Props:** `veredictos[]`, `supports`, `refutes`, `no_info`, `advertencia?`

**Filtros:** Todos / SUPPORTS / REFUTES / NO_INFO.

**Fila de veredicto (colapsada):**
- Badge con `SUPPORTS ✓` / `REFUTES ✕` / `NO_INFO ?`
- Texto de la cita (monospace, color accent)
- Justificación del LLM
- Badges RAGAS inline (F / AR / CP) con colores por umbral (verde ≥ 0.80, naranja ≥ 0.60, rojo < 0.60)
- Número de página

**Panel expandido (click en fila):**
1. Afirmación del tesista (`fragmento_oracion` o texto cita)
2. Paper citado (título, autores, año, DOI, página del paper)
3. Contenido original del paper (`fragmento_evidencia`) con similitud semántica
4. Motivo del veredicto con fondo colorado según tipo

---

### `components/auditoria/AlertasInconsistencias.jsx`

Muestra inconsistencias estructurales del documento:
- Citas sin referencia bibliográfica correspondiente.
- Referencias listadas que nunca se citan en el cuerpo.

---

### `components/auditoria/AlertasAlucinaciones.jsx`

Lista citas con veredicto `NO_INFO` que el sistema no pudo verificar (sin paper disponible en la base de conocimiento).

---

### `components/auditoria/MetricasRagas.jsx`

Panel de evaluación RAGAS y exportación del informe.

**Dos estados:**

**Sin métricas calculadas:**
- Descripción de las 3 métricas.
- Botón "Evaluar con RAGAS" → `auditoriaAPI.evaluarRagas()` + recarga métricas.
- Botón "Descargar informe Excel" → `auditoriaAPI.exportarMetricasExcel()` → descarga via `URL.createObjectURL`.

**Con métricas calculadas:**
- Tarjetas de 3 métricas con barra de progreso y valor numérico coloreado:
  - `Faithfulness`: ¿La auditoría está anclada en el paper sin alucinar?
  - `Answer Relevancy`: ¿El veredicto es relevante al claim verificado?
  - `Context Precision`: ¿El fragmento recuperado es pertinente al claim?
- Botones "Volver a evaluar" y "Descargar informe Excel".

**Descarga de Excel:** genera blob en el cliente con `URL.createObjectURL` + click programático. El archivo se nombra `informe_{documentoId}.xlsx`.

---

## 9. Sistema de diseño

### Variables CSS (`src/index.css`)

El tema es **oscuro** con variables CSS en `:root`. Las variables de color de acento se actualizan dinámicamente via JavaScript.

**Colores de fondo:**
- `--bg-base`: fondo general de la aplicación
- `--bg-surface`: superficies de cards y navbar
- `--bg-surface-2`: superficies secundarias dentro de cards

**Colores de texto:**
- `--text-primary`: texto principal
- `--text-secondary`: texto secundario
- `--text-muted`: texto tenue (hints, labels)

**Colores semánticos:**
- `--accent`, `--accent-hover`, `--accent-subtle`: color de acento (cambia dinámicamente)
- `--success`, `--success-subtle`: verde — SUPPORTS, verificado
- `--error`, `--error-subtle`: rojo — REFUTES, no encontrado
- `--warning`, `--warning-subtle`: naranja — NO_INFO, alertas
- `--border`: color de bordes

**Tipografía:**
- `--font-sans`: fuente sin serifa del sistema
- `--font-mono`: fuente monoespaciada (usada para textos de citas, DOIs, IDs)

**Radios:**
- `--radius-sm`, `--radius-md`, `--radius-lg`

### Animaciones CSS

```css
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } }
@keyframes spin   { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
```

`fadeIn` se usa en casi todos los componentes para entradas suaves. `spin` se usa en spinners de carga.

### Convenciones de estilo

- Todos los estilos son inline (`style={{}}`), no hay CSS modules ni clases utilitarias.
- Los colores se referencian exclusivamente mediante variables CSS para respetar el tema y el acento elegido.
- La escala tipográfica va desde `0.68rem` (labels, badges) hasta `2.2rem` (hero h1).

---

## 10. Flujo completo de usuario

```
1. PaginaInicio
   └─ Arrastra PDF → POST /ingesta/cargar → obtiene documento_id
      └─ Navega a PaginaAuditoria

2. PaginaAuditoria — Tab "Citas" (extracción en progreso)
   ├─ SSE conectado → muestra ProgresoAuditoria (0–100%)
   └─ Estado: listo_extraccion → carga datos fase 1

3. Tab "Citas" (fase 1 completada)
   ├─ Lista todas las citas detectadas con su estado
   ├─ Usuario selecciona referencias pendientes
   └─ Click "Verificar N referencia(s)" → POST /ingesta/{id}/verificar
      └─ SSE reconecta → verificando en CrossRef + Unpaywall

4. Tab "Referencias"
   └─ Lista referencias con nivel_confianza
      └─ Si "no_encontrado": subir PDF manualmente

5. Tab "Grafo"
   └─ Visualización interactiva ForceGraph2D
      └─ Toggles Referencias/Citas/Autores + filtro por veredicto, click en nodo para detalles

6. Tab "Motor" (fase 2 completada)
   ├─ Estado del motor (chunks indexados, citas vinculadas)
   └─ Click "Auditar" → POST /auditoria/{id}/auditar (LLM, puede tardar varios min)

7. Tab "Veredictos"
   ├─ Resumen: SUPPORTS / REFUTES / NO_INFO
   ├─ Filtros por tipo
   └─ Clic en fila → expande detalle (afirmación, paper, fragmento, motivo)

8. Tab "Alertas"
   ├─ Inconsistencias estructurales (citas sin ref, refs sin citar)
   └─ Alertas de verificación (citas NO_INFO)

9. Tab "Informe"
   ├─ (Opcional) Evaluar con RAGAS → POST /auditoria/{id}/evaluar-ragas
   ├─ Métricas promedio con barras visuales
   └─ Descargar informe Excel (Resumen + Citas con veredictos y scores)
```

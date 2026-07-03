# Frontend

SPA en React 19 + Vite (`frontend/`). Patrón de navegación: **workspace de documento único** con riel de fases vertical; el desbloqueo de cada fase depende del estado real que reporta el backend, nunca de lógica propia.

## Estructura

```
src/
  api/client.js        # axios centralizado (VITE_API_BASE_URL) + módulos por dominio
                       # interceptor: traduce ErrorResponse → {codigo, mensaje, accion}
  pages/
    PaginaInicio.jsx         # dropzone + documentos recientes (localStorage)
    WorkspaceDocumento.jsx   # layout: TopBar + RielFases + <Outlet/> con contexto
    PaginaProgreso.jsx       # fase ① — barra SSE de la extracción
    PaginaRevision.jsx       # fase ② — PDF real + cartillas editables (la pantalla clave)
    PaginaVerificar.jsx      # fase ③ — verificación por DOI + Zotero + papers manuales
    PaginaGrafo.jsx          # fase ④ — grafo interactivo con filtros por veredicto
    PaginaAuditoria.jsx      # fase ⑤ — lanzar auditoría + veredictos expandibles
    PaginaCierre.jsx         # fase ⑥ — resumen para el jurado + descarga del informe
    admin/                   # AdminLayout + PaginaRagas + PaginaEvaluacion
  components/          # TopBar, RielFases, VisorPDF, CartillaCita, CartillaReferencia,
                       # SelectorReferencia, BadgeVeredicto, TarjetaMetrica,
                       # BarraProgresoSSE, Estados (carga/vacío/error)
  hooks/useSSEProgreso.js    # SSE con reconexión + fallback a polling
  lib/                 # normalizar (búsqueda sin tildes), formato, fases (gating)
  store/               # temaStore (claro/oscuro), accentStore (color de acento),
                       # documentoStore (recientes en localStorage)
  index.css            # sistema de estilos con tokens CSS propios
```

## Rutas

| Ruta | Pantalla |
|---|---|
| `/` | Carga de PDF + recientes + salud del backend |
| `/doc/:id/progreso` | Fase ① Carga (SSE) |
| `/doc/:id/revision` | Fase ② Revisión humana |
| `/doc/:id/verificar` | Fase ③ Verificación/asociación de fuentes |
| `/doc/:id/grafo` | Fase ④ Grafo |
| `/doc/:id/auditoria` | Fase ⑤ Auditoría |
| `/doc/:id/cierre` | Fase ⑥ Cierre e informe |
| `/admin/ragas` | RAGAS (calidad interna) — **no enlazada desde el flujo** |
| `/admin/evaluacion` | Kappa/F1 vs experto — **no enlazada desde el flujo** |

## Gating de fases (`lib/fases.js`)

El estado del backend determina la última fase desbloqueada:

| Estado | Fases accesibles |
|---|---|
| `pendiente` / `procesando` | ① |
| `revision_pendiente` | ①–② |
| `listo_extraccion` / `verificando` | ①–③ |
| `completado` | todas |

El estado se obtiene leyendo una vez el endpoint SSE (`ingestaAPI.obtenerEstado`): el backend emite el estado actual y cierra si es terminal.

## La pantalla de revisión (fase ②)

Dos paneles:

- **Izquierda — `VisorPDF`** (react-pdf): el PDF real servido por `GET /ingesta/{id}/pdf`.
  - **Renderizado perezoso**: solo se montan las páginas a ±4 de la visible (IntersectionObserver); el resto son placeholders — imprescindible en tesis de 150+ páginas.
  - **Capa de texto activada**: se puede seleccionar y copiar texto hacia las cartillas.
  - Cada cita localizada es un **hotspot clicable** (subrayado sutil): clic → selecciona y hace scroll a su cartilla, sin mover el PDF.
  - Al hacer clic en una cartilla, el visor salta a `pagina_real` y el resaltado parpadea ~2 s (rects escalados con `ancho_pagina/alto_pagina`).
  - Botón "Ver referencias en el PDF" → salta a `pagina_referencias`.
- **Derecha — cartillas** (pestañas Citas/Referencias): edición directa con indicador de cambios sin guardar, `SelectorReferencia` con búsqueda insensible a tildes/mayúsculas (la normalización es solo para comparar, nunca reescribe el texto), filtro "solo sin vincular", alta manual, re-vinculación automática y "Confirmar datos y continuar".

## Convenciones de UI

- **Código de color de veredictos, único en toda la app** (definido en `BadgeVeredicto` y los tokens): SUPPORTS **verde**, REFUTES **rojo**, NO_INFO **gris**, advertencias **ámbar**.
- **Tokens CSS** (`index.css`): `--accent`, `--bg*`, `--text*`, `--border*`, colores de veredicto, radios y sombras. Tema claro por defecto + oscuro con `[data-tema='oscuro']`; el acento lo fija `accentStore`.
- **Tipografía**: sans para la UI (`--font-ui`), serif para texto de documento/paper (`--font-doc`, clase `.texto-doc`).
- **Errores**: siempre se muestra `mensaje` + `accion_sugerida` del backend (`ErrorInline` / `EstadoError`).
- **SSE robusto**: `useSSEProgreso` reintenta la conexión y cae a polling cada 3 s si la red falla; se detiene en estados terminales.

## Grafo (fase ④)

- Filtros conmutables por veredicto (Respaldadas/Refutadas/Sin evidencia/Sin auditar) con conteos; documento, referencias y autores siempre visibles.
- El **tamaño** de los nodos de cita es proporcional a su similitud con la evidencia; el tooltip la muestra.
- Detalle de nodo al hacer clic (metadatos de referencia o veredicto de cita).

## Administración (`/admin`)

Sin login (decisión de diseño): rutas separadas no enlazadas desde el flujo del usuario. Selector de documento compartido (recientes de localStorage o `documento_id` pegado a mano). RAGAS y la evaluación Kappa/F1 son **vistas separadas** — regla dura de no mezclarlas. La exportación Excel del admin incluye RAGAS (`incluir_ragas=true`); la del usuario, no.

## Build

```bash
npm run dev      # desarrollo (localhost:5173)
npm run lint     # ESLint
npm run build    # producción → dist/
```

`VITE_API_BASE_URL` (`.env`) apunta al backend; default `http://localhost:8000/api/v1`.

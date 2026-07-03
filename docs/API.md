# Referencia de la API

Base URL: **`/api/v1`** · Documentación interactiva: `GET /docs` (Swagger).

**Formato de error** (todos los endpoints): HTTP 4xx/5xx con

```json
{ "detail": { "codigo": "ESTADO_INVALIDO", "mensaje": "…", "accion_sugerida": "…" } }
```

## Salud

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | Nombre, versión y estado de la API (cuelga de la raíz, no de `/api/v1`) |
| GET | `/health` | `{"status": "ok"}` — lo usa el indicador "En línea" del frontend |

## Ingesta — `/ingesta`

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/cargar` | Sube el PDF (multipart `archivo`). Valida formato/tamaño, guarda en Storage y arranca la fase 1 en segundo plano. → `{documento_id, nombre_archivo, paginas, estado}` |
| GET | `/{id}/progreso` | **SSE** (`text/event-stream`). Emite `{estado, porcentaje, mensaje_progreso, error?}` y cierra en estados terminales. Sirve también como "GET estado". |
| GET | `/{id}/estructura` | Secciones detectadas del documento (introducción, metodología, referencias…) |
| GET | `/{id}/pdf` | El PDF original (`application/pdf`, inline) — lo consume el visor de la revisión |
| GET | `/{id}/markdown` | Markdown extraído (texto plano) |
| POST | `/{id}/confirmar-revision` | Cierra la revisión humana: `revision_pendiente → listo_extraccion` |
| POST | `/{id}/verificar` | Body `{referencia_ids: [...]}`. Verificación externa **solo por DOI** en segundo plano (estado `verificando`, seguir por SSE) |
| POST | `/{id}/importar-zotero` | Multipart `archivo` (`.ris` o `.zip` de Zotero con PDFs). Empareja, sobrescribe metadatos, asocia/indexa papers. Segundo plano + SSE |
| GET | `/{id}/importar-zotero/resultado` | Resumen de la última importación: emparejadas por DOI/título, PDFs del ZIP, descargas Unpaywall, sin texto, no emparejadas |

## Grafo — `/grafo`

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/{id}/referencias` | Referencias del documento (con `nivel_confianza`, `doi` = verificado si existe) |
| GET | `/{id}/citas` | Citas del documento (texto, tipo, página, aseveración, `referencia_id`) |
| GET | `/{id}/citas/ubicaciones` | Localización exacta de cada cita en el PDF: `pagina_real` + `rects[{pagina,x0,y0,x1,y1,ancho_pagina,alto_pagina}]`, más `pagina_referencias` (inicio de la sección de referencias). Cacheado |
| GET | `/{id}/resumen` | Conteos y densidad del grafo |
| GET | `/{id}/grafo-visual` | `{nodes[], links[]}` para el grafo interactivo (citas incluyen veredicto y similitud) |
| PATCH | `/{id}/citas/{cita_id}` | Edita una cita; `referencia_id` re-vincula (o `""` desvincula). Invalida el cache de ubicaciones |
| POST | `/{id}/citas` | Crea una cita manual |
| DELETE | `/{id}/citas/{cita_id}` | Elimina la cita y sus relaciones |
| PATCH | `/{id}/referencias/{ref_id}` | Edita una referencia (incl. `autores[]`, que recrea los nodos Autor) |
| POST | `/{id}/referencias` | Crea una referencia manual |
| DELETE | `/{id}/referencias/{ref_id}` | Elimina la referencia (y autores huérfanos) |
| POST | `/{id}/re-vincular` | Re-ejecuta la vinculación automática cita→referencia |
| POST | `/{id}/referencias/{ref_id}/paper` | Sube/**reemplaza** el PDF del paper (multipart). Borra los chunks del paper anterior e indexa el nuevo (`nivel_confianza: manual`) |
| DELETE | `/{id}/referencias/{ref_id}/paper` | **Desasocia** el paper: borra sus chunks y vuelve a `no_encontrado` |

## Recuperación — `/recuperacion`

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/{id}/motor/estado` | ¿El motor está listo? (papers indexados, cobertura) |
| POST | `/{id}/motor/consultar` | Body `{texto, n_resultados}`. Búsqueda semántica libre sobre los papers del documento |
| GET | `/{id}/motor/cita/{cita_id}` | Recuperación híbrida para una cita (contexto de grafo + chunks) |
| GET | `/{id}/motor/evidencia/{cita_id}` | Ruta de evidencia en el grafo para la cita |

## Auditoría — `/auditoria`

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/{id}/auditar` | Audita todas las citas con el LLM (síncrono, tarda minutos). Persiste veredictos en Neo4j |
| GET | `/{id}/veredictos` | Veredictos por cita: `SUPPORTS/REFUTES/NO_INFO`, justificación, evidencia, similitud, fuente |
| GET | `/{id}/alertas` | Citas sin referencia + referencias sin citar |
| GET | `/{id}/alertas/alucinaciones` | Citas `NO_INFO` (posibles alucinaciones del tesista) |
| POST | `/{id}/evaluar-ragas` | Evalúa RAGAS por cita (uso admin; síncrono, tarda minutos) |
| GET | `/{id}/metricas` | Promedios RAGAS del documento |
| GET | `/{id}/metricas/exportar` | **Informe Excel** (Resumen + Citas coloreadas por veredicto). Query `incluir_ragas=true` añade las métricas RAGAS (solo administración) |

## Evaluación vs experto — `/evaluacion` (separada de RAGAS)

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/{id}/evaluar` | Body `{etiquetas: [{cita_id, etiqueta_experto}]}`. Empareja con los veredictos del sistema y calcula matriz de confusión 3×3, P/R/F1 (categoría + macro + weighted) y Kappa de Cohen. Persiste el resultado |
| GET | `/{id}/resultados` | Última evaluación persistida |
| GET | `/{id}/exportar` | Excel con hojas Resumen y Matriz |

### Notas transversales

- Las etiquetas de veredicto son siempre `SUPPORTS`, `REFUTES`, `NO_INFO` (el frontend las traduce a Respaldada/Refutada/Sin evidencia).
- Los endpoints de escritura del grafo requieren que el documento esté en un estado compatible (p. ej. `verificar` exige `listo_extraccion` o `completado`).
- CORS: los orígenes permitidos se configuran en `backend/app/main.py`.

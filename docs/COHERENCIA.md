# Módulo de Coherencia Inter-Fuentes (diseño)

> Estado: **implementado** (2026-07-07). Backend: `models/coherencia.py`,
> `services/coherencia/coherencia_service.py`, `api/routes/coherencia.py`.
> Frontend: `components/CoherenciaFuentes.jsx` (sección en la fase Cierre).
> Pendiente: prueba manual E2E con una tesis auditada.
> Objetivo: multihop genuino sobre el grafo — relacionar las evidencias
> recuperadas de los distintos papers para detectar si las fuentes de la tesis
> se apoyan, se contradicen o ni siquiera se relacionan entre sí.

## 1. Motivación

La auditoría actual (EP-004) evalúa cada cita **en aislamiento**: aseveración
del tesista vs. fragmento de su paper. El grafo solo se usa como índice de un
salto (`Cita→CITA_A→Referencia→DOI`) para filtrar la búsqueda vectorial.

Este módulo crea conocimiento de **segundo orden**: relaciones entre las
evidencias mismas. Preguntas que solo se responden componiendo relaciones del
grafo (multihop real):

- ¿Dos fuentes citadas en el marco teórico se contradicen entre sí?
- ¿Qué afirmaciones del tesista están trianguladas (≥2 fuentes convergen)?
- ¿Qué referencias son "islas" que no comparten ningún concepto con el resto?

División de trabajo que evita la explosión N² de comparar todo contra todo:
**el grafo genera los candidatos por traversal (pares de evidencias que
comparten concepto), el LLM solo etiqueta las aristas de esos pares.**

## 2. Insumos (ya existen, no hay retrieval nuevo)

Tras `auditar_documento` (EP-004), cada nodo `Cita` ya persiste:

| Propiedad             | Origen                                    |
|-----------------------|-------------------------------------------|
| `fragmento`           | aseveración del tesista (extracción)      |
| `veredicto`           | SUPPORTS / REFUTES / NO_INFO              |
| `fragmento_evidencia` | chunk(s) recuperados del paper            |
| `pagina_paper`        | página del chunk en el paper              |
| `similitud`           | score de la recuperación                  |

Solo participan citas con veredicto **SUPPORTS o REFUTES** (NO_INFO no aporta
evidencia). Las evidencias idénticas (mismo DOI + texto normalizado) se
deduplican en un solo nodo `Evidencia`.

## 3. Esquema del grafo

### Nodos nuevos

```
(:Concepto {
  id: string (uuid),
  documento_id: string,        // scoping por tesis: no se fusionan conceptos entre documentos
  nombre: string,              // forma canónica ("kappa de Cohen")
  variantes: [string],         // formas fusionadas ("coeficiente kappa", ...)
  n_referencias: int           // # de referencias distintas que lo mencionan (denormalizado para la UI)
})

(:Evidencia {
  id: string (uuid),
  documento_id: string,
  texto: string,               // fragmento_evidencia (recortado a ~1500 chars)
  pagina_paper: int | null,
  doi: string | null
})
```

### Aristas nuevas

```
(d:Documento)-[:TIENE_CONCEPTO]->(k:Concepto)
(c:Cita)-[:SOBRE]->(k:Concepto)              // conceptos de la ASEVERACIÓN del tesista
(e:Evidencia)-[:MENCIONA]->(k:Concepto)      // conceptos del fragmento del PAPER
(c:Cita)-[:EVIDENCIADA_POR]->(e:Evidencia)
(e:Evidencia)-[:PROVIENE_DE]->(r:Referencia)

(e1:Evidencia)-[rel:APOYA|CONTRADICE|COMPLEMENTA]->(e2:Evidencia)
// propiedades de rel:
//   confianza:     "alta" | "media"
//   justificacion: string (1 oración del juez)
//   concepto_id:   string (el concepto compartido que originó la comparación)
//   evaluado_en:   datetime
```

Decisiones:

- **NO_RELACIONADO no se persiste** — la ausencia de arista ya lo expresa.
- **Las relaciones a nivel Referencia (CONVERGE_CON / DIVERGE_DE) no se
  materializan**: se derivan en tiempo de consulta desde las aristas entre
  evidencias, para que nunca queden desactualizadas (§6).
- Los embeddings de conceptos/evidencias **no se persisten en Neo4j**: solo se
  usan en memoria durante la construcción (fusión de sinónimos y poda).
- El subgrafo es **por documento** (`documento_id` en Concepto y Evidencia):
  reconstruirlo = `DETACH DELETE` del subgrafo del documento y recalcular.

### Diagrama

```
Documento ─TIENE_CITA→ Cita ─CITA_A→ Referencia ─ESCRITO_POR→ Autor
    │                   │  │              ↑
    │                   │  └─EVIDENCIADA_POR→ Evidencia ─PROVIENE_DE┘
    │                   │                        │  │
    │                   └────SOBRE→ Concepto ←MENCIONA┘
    └─TIENE_CONCEPTO────────────────↑
                                    Evidencia ─APOYA|CONTRADICE|COMPLEMENTA→ Evidencia
```

## 4. Flujo del módulo (7 pasos)

Servicio nuevo: `backend/app/services/coherencia/coherencia_service.py`.
Se ejecuta **después de la auditoría** (estado `completado`), disparado por el
usuario. Sigue el patrón de las otras fases: tarea en segundo plano + progreso
+ cache JSON en Storage.

```
1. RECOLECCIÓN        Cypher: citas con veredicto ∈ {SUPPORTS, REFUTES} +
                      fragmento_evidencia + referencia. Dedup → nodos Evidencia.

2. CONCEPTOS (LLM)    2-4 conceptos por texto, en lotes de ~10 textos por
                      llamada. Se extraen de: (a) cada Evidencia, (b) la
                      aseveración (fragmento) de cada Cita.

3. FUSIÓN             embedding_service sobre cada concepto; union-find de
                      pares con similitud ≥ 0.90 → forma canónica = la más
                      frecuente; el resto queda en `variantes`. Persistir
                      Concepto + aristas SOBRE / MENCIONA.

4. CANDIDATOS         Traversal puro (sin LLM): pares (e1, e2) que comparten
   (multihop 2 saltos) ≥1 Concepto Y provienen de Referencias distintas.
                      Podas anti-clique (§5).

5. JUEZ DE ARISTAS    LLM en paralelo (ThreadPoolExecutor, max_workers=5,
   (LLM)              como auditoria_service): APOYA / CONTRADICE /
                      COMPLEMENTA / NO_RELACIONADO + justificación + confianza.
                      Persistir solo ≠ NO_RELACIONADO.

6. HALLAZGOS          Cypher multihop puro (§6): contradicciones internas,
                      triangulaciones, fuentes isla, conceptos débiles.

7. PERSISTENCIA       JSON en Storage `coherencia/{documento_id}.json`
                      (hallazgos + métricas + grafo serializado para la UI),
                      versionado como el cache de ubicaciones.
```

### Query del paso 4 (generación de candidatos)

```cypher
MATCH (d:Documento {id: $doc_id})-[:TIENE_CONCEPTO]->(k:Concepto),
      (e1:Evidencia)-[:MENCIONA]->(k)<-[:MENCIONA]-(e2:Evidencia),
      (e1)-[:PROVIENE_DE]->(r1:Referencia),
      (e2)-[:PROVIENE_DE]->(r2:Referencia)
WHERE e1.id < e2.id AND r1.id <> r2.id
RETURN DISTINCT e1.id, e2.id, collect(DISTINCT k.id) AS conceptos_compartidos
```

## 5. Podas anti-clique (conceptos comodín)

Un concepto genérico ("educación", "aprendizaje") puede conectar demasiadas
evidencias y generar cientos de pares. Tres válvulas, en orden:

1. **Descartar conceptos ubicuos**: si un concepto lo mencionan > 60% de las
   referencias del documento, no genera candidatos (es el tema de la tesis,
   no un punto de comparación).
2. **Cap por concepto**: si un concepto conecta > 8 evidencias, solo se
   comparan los pares con mayor similitud coseno entre evidencias (top-15
   pares por concepto).
3. **Presupuesto global**: máximo ~120 pares por documento; si se excede, se
   priorizan pares donde alguna cita tuvo veredicto REFUTES (ahí es más
   probable encontrar conflicto informativo).

## 6. Hallazgos derivados (el multihop de consulta)

Todos son Cypher puro sobre el grafo construido — ninguna llamada LLM extra.

**Contradicción interna** (gravedad: alta) — dos citas del tesista se apoyan
en fuentes que se refutan entre sí:

```cypher
MATCH (d:Documento {id: $doc_id})-[:TIENE_CITA]->(c1:Cita)
      -[:EVIDENCIADA_POR]->(e1:Evidencia)
      -[rel:CONTRADICE]-(e2:Evidencia)
      <-[:EVIDENCIADA_POR]-(c2:Cita)<-[:TIENE_CITA]-(d)
MATCH (e1)-[:PROVIENE_DE]->(r1), (e2)-[:PROVIENE_DE]->(r2)
RETURN c1.id, c2.id, r1.titulo, r2.titulo, rel.justificacion, rel.confianza
```

**Triangulación** (refuerzo positivo) — la evidencia de una cita es apoyada
por evidencia de otra referencia distinta:

```cypher
MATCH (c:Cita)-[:EVIDENCIADA_POR]->(e1:Evidencia)-[rel:APOYA]-(e2:Evidencia),
      (e1)-[:PROVIENE_DE]->(r1), (e2)-[:PROVIENE_DE]->(r2)
WHERE r1.id <> r2.id AND c.veredicto = 'SUPPORTS'
RETURN c.id, r1.titulo AS fuente, collect(r2.titulo) AS convergen
```

**Fuente isla** (aviso) — referencia citada cuyas evidencias no comparten
ningún concepto con evidencias de otras referencias:

```cypher
MATCH (d:Documento {id: $doc_id})-[:TIENE_REFERENCIA]->(r:Referencia)
      <-[:PROVIENE_DE]-(e:Evidencia)
WHERE NOT EXISTS {
  MATCH (e)-[:MENCIONA]->(:Concepto)<-[:MENCIONA]-(e2:Evidencia)
        -[:PROVIENE_DE]->(r2:Referencia)
  WHERE r2.id <> r.id
}
RETURN r.id, r.titulo
```

**Convergencia/divergencia entre referencias** (derivada, para la UI):

```cypher
MATCH (e1:Evidencia)-[rel]-(e2:Evidencia),
      (e1)-[:PROVIENE_DE]->(r1), (e2)-[:PROVIENE_DE]->(r2)
WHERE r1.id < r2.id AND type(rel) IN ['APOYA','CONTRADICE','COMPLEMENTA']
RETURN r1.id, r2.id,
       sum(CASE type(rel) WHEN 'APOYA' THEN 1 ELSE 0 END)      AS apoyos,
       sum(CASE type(rel) WHEN 'CONTRADICE' THEN 1 ELSE 0 END) AS contradicciones
```

**Concepto débil** (aviso) — concepto central de la argumentación sostenido
por una sola referencia (`n_referencias = 1` y ≥2 citas SOBRE él).

## 7. Prompts (reglas duras)

**SYSTEM_CONCEPTOS** — extractor por lotes:
- 2 a 4 conceptos por texto; sustantivos o frases nominales cortas (≤4 palabras),
  en español, sin adjetivos valorativos.
- Prohibido devolver el tema global de la tesis como concepto.
- Salida: un JSON por línea `{"texto_id": "...", "conceptos": ["...", "..."]}`.

**SYSTEM_RELACION** — juez de pares, **conservador por diseño**:
- `CONTRADICE` solo ante incompatibilidad explícita (afirmaciones que no pueden
  ser ciertas a la vez). La diferencia de matiz, alcance o población → `COMPLEMENTA`.
- `APOYA` solo si sostienen la misma conclusión sobre el mismo objeto.
- Ante duda: `NO_RELACIONADO`. Un falso CONTRADICE es peor que uno omitido
  (es una acusación de inconsistencia al tesista).
- Salida: `RELACION: <...>` / `CONFIANZA: <alta|media>` / `JUSTIFICACION: <1 oración>`
  (mismo estilo parseable que el juez de auditoría).

## 8. API y modelos

```
POST /coherencia/{documento_id}          → dispara la construcción (segundo plano)
GET  /coherencia/{documento_id}          → hallazgos + métricas (del cache JSON)
GET  /coherencia/{documento_id}/grafo    → nodos/aristas serializados para la visualización
```

Modelos Pydantic (`models/coherencia.py`):

```python
class TipoHallazgo(str, Enum):
    CONTRADICCION_INTERNA = "contradiccion_interna"   # gravedad alta
    TRIANGULACION         = "triangulacion"           # positivo
    FUENTE_ISLA           = "fuente_isla"             # aviso
    CONCEPTO_DEBIL        = "concepto_debil"          # aviso

class HallazgoCoherencia(BaseModel):
    tipo: TipoHallazgo
    cita_ids: list[str]
    referencia_ids: list[str]
    concepto: str | None
    justificacion: str
    confianza: str | None            # de la arista LLM, si aplica

class CoherenciaResponse(BaseModel):
    documento_id: str
    construido_en: datetime | None
    total_evidencias: int
    total_conceptos: int
    total_relaciones: int            # aristas APOYA/CONTRADICE/COMPLEMENTA
    hallazgos: list[HallazgoCoherencia]
```

## 9. Frontend

- **Ubicación**: sección dentro de la fase **Cierre** (no una fase nueva del
  riel: no requiere estado nuevo del backend ni cambia el gating de `fases.js`).
  Botón "Analizar coherencia entre fuentes" → progreso → resultados.
- **Tecnología**: `react-force-graph-2d` (ya instalada) con los mismos
  patrones de PaginaGrafo: panel de detalle flotante, filtros como badges
  clicables, colores del sistema de veredictos.

### 9.1 Proyección: mapa de referencias, no el grafo crudo

Pintar el grafo completo del módulo (evidencias + conceptos + citas +
referencias + aristas estructurales) produce una bola de pelos donde las
aristas semánticas quedan enterradas. La vista principal proyecta **solo las
Referencias** como nodos, con las relaciones **derivadas** entre ellas como
aristas (query de convergencia/divergencia de §6):

```
        [Pepe 2020] ────verde──── [Juan 2019]        verde  = APOYA
             │                        │              rojo   = CONTRADICE
            rojo                    azul             azul   = COMPLEMENTA
             │                        │              gris ┄ = co-mención de
        [Silva 2021] ┄┄┄gris┄┄┄ [Rojas 2022]                 conceptos sin
                                                             relación juzgada
```

- **Grosor de arista** = número de pares de evidencias que la sustentan.
- **Tamaño de nodo** = número de citas del tesista que dependen de ese paper.
- **Color de nodo** = neutro; ámbar si es "fuente isla".

### 9.2 Drill-down: las evidencias viven en el panel, no en el lienzo

Clic en una **arista** → panel lateral (patrón `DetalleNodo` de PaginaGrafo,
vía `onLinkClick`) con los pares de fragmentos enfrentados: texto literal de
cada paper, justificación del juez, confianza, concepto compartido, y links a
las citas del tesista involucradas. El lienzo dice *quién* se contradice; el
panel dice *dónde y por qué*.

Clic en un **nodo** → panel con la referencia, sus citas dependientes y sus
conceptos.

### 9.3 Filtros (badges clicables, patrón FILTROS_VEREDICTO)

1. **Por tipo de relación**: Apoyan (verde) / Contradicen (rojo) /
   Complementan (azul) / Co-mención (gris), con conteos. Activar solo
   "Contradicen" deja el mapa de conflictos en un clic.
2. **Por concepto**: chips con los conceptos del documento ordenados por
   `n_referencias`; al elegir uno, el grafo se reduce a las referencias que lo
   mencionan (la vista "estrella por concepto" como filtro, no como layout
   aparte).

### 9.4 Cartillas de hallazgos

Lista de hallazgos junto al lienzo (mismo patrón de cartillas de la revisión):
badge por tipo (rojo contradicción interna, verde triangulación, ámbar
isla/concepto débil), justificación del juez y referencias involucradas.
Clic en una cartilla → `centerAt`/`zoom` de ForceGraph2D sobre los nodos
involucrados y resaltado de la arista — el mismo gesto que "clic en cartilla →
el PDF salta a la cita" de la fase Revisión.

### 9.5 Diferencia conceptual con la fase Grafo

La fase Grafo muestra la estructura del documento ("¿cómo está armada la
tesis?"). Este mapa muestra papers conectados por lo que **dicen**
("¿la literatura que usa se sostiene como conjunto?"). Misma tecnología,
distinta proyección.

## 10. Costos e invalidación

Tesis típica (~50 citas auditadas, ~40 evidencias únicas):

| Paso                | Llamadas                                    |
|---------------------|---------------------------------------------|
| Conceptos (lotes)   | ~9 LLM (40 evidencias + 50 aseveraciones)/10 |
| Embeddings (fusión) | ~200 textos cortos (baratos)                 |
| Juez de pares       | ~40–80 LLM                                   |
| Hallazgos           | 0 (Cypher puro)                              |

Total ≈ una auditoría completa. Aceptable como paso opcional post-auditoría.

Invalidación: editar/eliminar citas o referencias, o re-auditar, invalida el
cache `coherencia/{documento_id}.json` (mismo mecanismo versionado que
`ubicaciones/`). El subgrafo en Neo4j se reconstruye completo en cada corrida
(idempotente: `DETACH DELETE` por `documento_id` antes de escribir).

## 11. Qué lo hace GraphRAG (defensa)

1. **Construcción**: el traversal `Evidencia→Concepto←Evidencia` (2 saltos)
   genera los candidatos; sin el grafo, el mismo resultado costaría O(N²)
   comparaciones LLM.
2. **Consulta**: los hallazgos son caminos de 4–6 saltos
   (`Cita→Evidencia→CONTRADICE→Evidencia→Referencia→Cita`) imposibles de
   expresar como búsqueda vectorial: la respuesta emerge de componer
   relaciones, y los nodos intermedios (conceptos, evidencias) se descubren
   durante el proceso.
3. **Producto**: el mapa de acuerdos/contradicciones entre fuentes es un
   entregable que un RAG plano no puede producir.

Complementos ya diseñados que suman al argumento: ventana de contexto
`chunk_index ± 1` en la recuperación (mejora de evidencia, no multihop) y, a
futuro, la red de citaciones `(:Referencia)-[:REFERENCIA_A]->(:Referencia)`
vía CrossRef/OpenAlex para detectar citas de segunda mano con traversal de
longitud variable.

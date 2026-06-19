import { useRef, useState, useEffect, useCallback, useMemo } from 'react'
import ForceGraph2D from 'react-force-graph-2d'

// ── Colores ──────────────────────────────────────────────────────────────────

const COLOR_REFERENCIA = {
  texto_completo: '#10b981',
  abstract:       '#06b6d4',
  cache:          '#06b6d4',
  manual:         '#8b5cf6',
  no_encontrado:  '#ef4444',
  _pendiente:     '#6b7280',
}

const COLOR_NODO = {
  Documento: '#3b82f6',
  Cita:      '#f59e0b',
  Autor:     '#a855f7',
}

// Color de la Cita según el veredicto de auditoría (si ya fue auditada)
const COLOR_VEREDICTO = {
  SUPPORTS: '#10b981',
  REFUTES:  '#ef4444',
  NO_INFO:  '#f59e0b',
}

const COLOR_LINK = {
  TIENE_REFERENCIA: '#3b82f640',
  TIENE_CITA:       '#f59e0b35',
  CITA_A:           '#10b98155',
  ESCRITO_POR:      '#a855f735',
}

const RADIO = { Documento: 11, Referencia: 8, Cita: 5, Autor: 4 }

// Rango de radio para las Citas, escalado por la similitud del RAG (0..1)
const RADIO_CITA_MIN = 3
const RADIO_CITA_MAX = 10

function colorNodo(node) {
  if (node.tipo === 'Referencia')
    return COLOR_REFERENCIA[node.nivel_confianza] ?? COLOR_REFERENCIA._pendiente
  if (node.tipo === 'Cita' && node.veredicto)
    return COLOR_VEREDICTO[node.veredicto] ?? COLOR_NODO.Cita
  return COLOR_NODO[node.tipo] ?? '#6b7280'
}

// Radio del nodo. Las Citas se dimensionan por la similitud del fragmento
// recuperado (mejor evidencia del RAG → nodo más grande).
function radioNodo(node) {
  if (node.tipo === 'Cita') {
    const s = typeof node.similitud === 'number' ? node.similitud : null
    if (s === null) return RADIO.Cita
    const clamp = Math.max(0, Math.min(1, s))
    return RADIO_CITA_MIN + clamp * (RADIO_CITA_MAX - RADIO_CITA_MIN)
  }
  return RADIO[node.tipo] ?? 5
}

// ── Leyenda ──────────────────────────────────────────────────────────────────

const LEYENDA = [
  { label: 'Documento',          color: '#3b82f6' },
  { label: 'Ref. verificada',    color: '#10b981' },
  { label: 'Ref. abstract',      color: '#06b6d4' },
  { label: 'Ref. no encontrada', color: '#ef4444' },
  { label: 'Ref. pendiente',     color: '#6b7280' },
  { label: 'Cita ✓ SUPPORTS',    color: '#10b981' },
  { label: 'Cita ✕ REFUTES',     color: '#ef4444' },
  { label: 'Cita ? / sin auditar', color: '#f59e0b' },
  { label: 'Autor',              color: '#a855f7' },
]

// Opciones del filtro de Citas por veredicto
const FILTROS_VEREDICTO = [
  { id: 'todos',       label: 'Todos' },
  { id: 'SUPPORTS',    label: 'SUPPORTS ✓' },
  { id: 'REFUTES',     label: 'REFUTES ✕' },
  { id: 'NO_INFO',     label: 'NO_INFO ?' },
  { id: 'sin_auditar', label: 'Sin auditar' },
]

// ── Componente ───────────────────────────────────────────────────────────────

const ALTURA = 580

export default function GrafoVisual({ data }) {
  const containerRef = useRef()
  const [ancho, setAncho]               = useState(0)
  const [nodoSel, setNodoSel]           = useState(null)
  const [mostrarReferencias, setMostrarReferencias] = useState(true)
  const [mostrarCitas, setMostrarCitas] = useState(true)
  const [mostrarAutores, setMostrarAutores] = useState(false)
  // Filtro de Citas por veredicto: 'todos' | 'SUPPORTS' | 'REFUTES' | 'NO_INFO' | 'sin_auditar'
  const [filtroVeredicto, setFiltroVeredicto] = useState('todos')

  // Medir ancho real del contenedor
  useEffect(() => {
    if (!containerRef.current) return
    const obs = new ResizeObserver(entries => setAncho(entries[0].contentRect.width))
    obs.observe(containerRef.current)
    return () => obs.disconnect()
  }, [])

  // Filtrar nodos/links según toggles y filtro de veredicto
  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] }

    const visibles = data.nodes.filter(n => {
      if (n.tipo === 'Referencia' && !mostrarReferencias) return false
      if (n.tipo === 'Cita'       && !mostrarCitas)       return false
      if (n.tipo === 'Autor'      && !mostrarAutores)     return false
      // Filtro por veredicto sólo aplica a las Citas
      if (n.tipo === 'Cita' && filtroVeredicto !== 'todos') {
        if (filtroVeredicto === 'sin_auditar') return !n.veredicto
        return n.veredicto === filtroVeredicto
      }
      return true
    })

    const ids = new Set(visibles.map(n => n.id))
    return {
      nodes: visibles,
      links: data.links.filter(l => {
        const src = typeof l.source === 'object' ? l.source.id : l.source
        const tgt = typeof l.target === 'object' ? l.target.id : l.target
        return ids.has(src) && ids.has(tgt)
      }),
    }
  }, [data, mostrarReferencias, mostrarCitas, mostrarAutores, filtroVeredicto])

  // Renderizado custom de cada nodo sobre canvas
  const paintNode = useCallback((node, ctx, globalScale) => {
    const r     = radioNodo(node)
    const color = colorNodo(node)

    // Círculo relleno
    ctx.beginPath()
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
    ctx.fillStyle = color
    ctx.fill()

    // Borde para el Documento
    if (node.tipo === 'Documento') {
      ctx.strokeStyle = 'rgba(255,255,255,0.5)'
      ctx.lineWidth   = 1.5 / globalScale
      ctx.stroke()
    }

    // Etiqueta: siempre en Documento, en el resto solo con suficiente zoom
    if (node.tipo === 'Documento' || globalScale > 1.5) {
      const fs = Math.max(2.5, 10 / globalScale)
      ctx.font          = `${fs}px sans-serif`
      ctx.textAlign     = 'center'
      ctx.textBaseline  = 'top'
      ctx.fillStyle     = 'rgba(255,255,255,0.82)'
      ctx.fillText((node.label ?? '').slice(0, 20), node.x, node.y + r + 1.5)
    }
  }, [])

  // Área de click: un poco más grande que el círculo visible
  const paintPointer = useCallback((node, color, ctx) => {
    const r = radioNodo(node) + 3
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
    ctx.fill()
  }, [])

  const handleClickNodo = useCallback(node => {
    setNodoSel(prev => (prev?.id === node.id ? null : node))
  }, [])

  if (!data) return <SpinnerGrafo />

  const stats = {
    nodos: graphData.nodes.length,
    links: graphData.links.length,
  }

  return (
    <div>
      {/* Barra superior: leyenda + toggles */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', gap: '0.75rem', marginBottom: '0.75rem',
      }}>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          {LEYENDA.map(({ label, color }) => (
            <span key={label} style={{
              display: 'flex', alignItems: 'center', gap: '0.3rem',
              fontSize: '0.68rem', color: 'var(--text-muted)',
            }}>
              <span style={{
                width: 8, height: 8, borderRadius: '50%',
                background: color, display: 'inline-block', flexShrink: 0,
              }} />
              {label}
            </span>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span
            title="El tamaño de cada Cita refleja la similitud del fragmento recuperado por el RAG"
            style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}
          >
            {stats.nodos} nodos · {stats.links} enlaces · ⌀ cita ∝ similitud
          </span>
          <Toggle activo={mostrarReferencias} onChange={setMostrarReferencias} label="Referencias" />
          <Toggle activo={mostrarCitas}    onChange={setMostrarCitas}    label="Citas" />
          <Toggle activo={mostrarAutores}  onChange={setMostrarAutores}  label="Autores" />
        </div>
      </div>

      {/* Segunda fila: filtro de Citas por veredicto */}
      {mostrarCitas && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.4rem',
          flexWrap: 'wrap', marginBottom: '0.75rem',
        }}>
          <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>Veredicto:</span>
          {FILTROS_VEREDICTO.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => setFiltroVeredicto(id)}
              style={{
                padding: '0.18rem 0.6rem', fontSize: '0.68rem',
                borderRadius: '999px',
                border: `1px solid ${filtroVeredicto === id ? 'var(--accent)' : 'var(--border)'}`,
                background: filtroVeredicto === id ? 'var(--accent-subtle)' : 'transparent',
                color: filtroVeredicto === id ? 'var(--accent)' : 'var(--text-muted)',
                cursor: 'pointer', fontFamily: 'var(--font-sans)', transition: 'all 0.15s',
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {/* Canvas del grafo */}
      <div
        ref={containerRef}
        style={{
          width: '100%', height: ALTURA,
          borderRadius: 'var(--radius-md)',
          overflow: 'hidden',
          background: '#0f172a',
          border: '1px solid rgba(255,255,255,0.06)',
        }}
      >
        {ancho > 0 && (
          <ForceGraph2D
            graphData={graphData}
            width={ancho}
            height={ALTURA}
            backgroundColor="#0f172a"
            nodeCanvasObject={paintNode}
            nodePointerAreaPaint={paintPointer}
            linkColor={link => COLOR_LINK[link.tipo] ?? '#ffffff20'}
            linkDirectionalArrowLength={4}
            linkDirectionalArrowRelPos={1}
            linkWidth={1}
            onNodeClick={handleClickNodo}
            nodeLabel=""
            cooldownTicks={150}
            d3AlphaDecay={0.02}
            d3VelocityDecay={0.3}
          />
        )}
      </div>

      {/* Panel de detalle del nodo seleccionado */}
      {nodoSel && (
        <div style={{
          marginTop: '0.75rem', padding: '0.875rem',
          background: 'var(--bg-surface-2)',
          borderRadius: 'var(--radius-md)',
          border: `1px solid ${colorNodo(nodoSel)}40`,
          animation: 'fadeIn 0.2s ease forwards',
        }}>
          <div style={{
            display: 'flex', justifyContent: 'space-between',
            alignItems: 'center', marginBottom: '0.625rem',
          }}>
            <span style={{
              fontSize: '0.68rem', fontWeight: 700,
              textTransform: 'uppercase', letterSpacing: '0.06em',
              color: colorNodo(nodoSel),
              padding: '0.15rem 0.55rem', borderRadius: '999px',
              background: colorNodo(nodoSel) + '18',
            }}>
              {nodoSel.tipo}
            </span>
            <button
              onClick={() => setNodoSel(null)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '1rem', lineHeight: 1 }}
            >
              ✕
            </button>
          </div>
          <DetalleNodo nodo={nodoSel} />
        </div>
      )}
    </div>
  )
}

// ── Sub-componentes ──────────────────────────────────────────────────────────

const ETIQUETA_VEREDICTO = {
  SUPPORTS: { texto: 'SUPPORTS ✓', color: '#10b981' },
  REFUTES:  { texto: 'REFUTES ✕',  color: '#ef4444' },
  NO_INFO:  { texto: 'NO_INFO ?',  color: '#f59e0b' },
}

const pct = v => (typeof v === 'number' ? `${(v * 100).toFixed(0)}%` : null)

function DetalleNodo({ nodo }) {
  // Filas de texto plano clave→valor, según el tipo de nodo
  const filas = [
    nodo.tipo === 'Documento'  && ['Archivo',    nodo.label],
    nodo.tipo === 'Referencia' && ['Referencia', nodo.label],
    nodo.titulo                && ['Título',     nodo.titulo],
    nodo.tipo === 'Referencia' && nodo.autores?.length && ['Autores', nodo.autores.join('; ')],
    nodo.tipo === 'Referencia' && nodo.anio       && ['Año',      String(nodo.anio)],
    nodo.tipo === 'Referencia' && nodo.fuente     && ['Fuente',   nodo.fuente],
    nodo.tipo === 'Referencia' && nodo.doi        && ['DOI',      nodo.doi],
    nodo.nivel_confianza       && ['Cobertura',  nodo.nivel_confianza.replace('_', ' ')],
    nodo.tipo === 'Referencia' && typeof nodo.score_crossref === 'number'
                               && ['Score CrossRef', nodo.score_crossref.toFixed(2)],
    nodo.tipo === 'Cita'       && ['Cita',       nodo.label],
    nodo.tipo_cita             && ['Tipo',       nodo.tipo_cita],
    nodo.tipo === 'Cita' && nodo.pagina != null && ['Página', String(nodo.pagina)],
    nodo.tipo === 'Autor'      && ['Autor',      nodo.label],
  ].filter(Boolean)

  const ver = nodo.tipo === 'Cita' && nodo.veredicto ? ETIQUETA_VEREDICTO[nodo.veredicto] : null

  // Métricas numéricas de la cita (similitud RAG + RAGAS), si existen
  const metricas = nodo.tipo === 'Cita' ? [
    ['Similitud RAG', pct(nodo.similitud)],
    ['Faithfulness',  pct(nodo.faithfulness)],
    ['Answer Rel.',   pct(nodo.answer_relevancy)],
    ['Context Prec.', pct(nodo.context_precision)],
  ].filter(([, v]) => v !== null) : []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.55rem' }}>
      {/* Badge de veredicto */}
      {ver && (
        <span style={{
          alignSelf: 'flex-start',
          fontSize: '0.7rem', fontWeight: 700, fontFamily: 'var(--font-mono)',
          color: ver.color, background: ver.color + '1F',
          padding: '0.15rem 0.55rem', borderRadius: '999px',
        }}>
          {ver.texto}
        </span>
      )}

      {/* Filas clave→valor */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
        {filas.map(([k, v]) => (
          <div key={k} style={{ display: 'flex', gap: '0.75rem', fontSize: '0.8rem', lineHeight: 1.5 }}>
            <span style={{ color: 'var(--text-muted)', minWidth: '6rem', flexShrink: 0 }}>{k}</span>
            <span style={{ color: 'var(--text-primary)', wordBreak: 'break-word' }}>{v}</span>
          </div>
        ))}
      </div>

      {/* Afirmación del tesista (oración que contiene la cita) */}
      {nodo.tipo === 'Cita' && nodo.fragmento && (
        <BloqueTexto label="Afirmación del tesista" texto={nodo.fragmento} acento="var(--accent)" />
      )}

      {/* Contenido original del paper recuperado por el RAG */}
      {nodo.tipo === 'Cita' && nodo.fragmento_evidencia && (
        <BloqueTexto
          label={nodo.pagina_paper != null
            ? `Contenido del paper (pág. ${nodo.pagina_paper})`
            : 'Contenido del paper'}
          texto={nodo.fragmento_evidencia}
          acento="var(--success)"
        />
      )}

      {/* Justificación del veredicto */}
      {nodo.tipo === 'Cita' && nodo.justificacion && (
        <div style={{ fontSize: '0.8rem', lineHeight: 1.5 }}>
          <span style={{ color: 'var(--text-muted)' }}>Motivo</span>
          <p style={{ margin: '0.2rem 0 0', color: 'var(--text-primary)' }}>{nodo.justificacion}</p>
        </div>
      )}

      {/* Métricas: similitud RAG + RAGAS */}
      {metricas.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
          {metricas.map(([k, v]) => (
            <span key={k} style={{
              fontSize: '0.68rem', fontFamily: 'var(--font-mono)',
              color: 'var(--text-secondary)', background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              padding: '0.15rem 0.5rem', borderRadius: 'var(--radius-sm)',
            }}>
              {k} <strong style={{ color: 'var(--text-primary)' }}>{v}</strong>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function BloqueTexto({ label, texto, acento }) {
  return (
    <div style={{ fontSize: '0.8rem', lineHeight: 1.55 }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <p style={{
        margin: '0.25rem 0 0', padding: '0.5rem 0.7rem',
        color: 'var(--text-primary)',
        background: 'var(--bg-surface)',
        borderLeft: `2px solid ${acento}`,
        borderRadius: 'var(--radius-sm)',
        wordBreak: 'break-word',
      }}>
        {texto}
      </p>
    </div>
  )
}

function Toggle({ activo, onChange, label }) {
  return (
    <button
      onClick={() => onChange(v => !v)}
      style={{
        padding: '0.2rem 0.65rem', fontSize: '0.7rem',
        borderRadius: '999px',
        border: `1px solid ${activo ? 'var(--accent)' : 'var(--border)'}`,
        background: activo ? 'var(--accent-subtle)' : 'transparent',
        color: activo ? 'var(--accent)' : 'var(--text-muted)',
        cursor: 'pointer', fontFamily: 'var(--font-sans)', transition: 'all 0.15s',
      }}
    >
      {label}
    </button>
  )
}

function SpinnerGrafo() {
  return (
    <div style={{
      height: ALTURA, display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: '#0f172a', borderRadius: 'var(--radius-md)',
      border: '1px solid rgba(255,255,255,0.06)',
    }}>
      <div style={{
        width: 32, height: 32,
        border: '3px solid rgba(255,255,255,0.1)',
        borderTop: '3px solid #3b82f6',
        borderRadius: '50%', animation: 'spin 1s linear infinite',
      }} />
    </div>
  )
}

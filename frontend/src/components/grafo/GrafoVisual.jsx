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

const COLOR_LINK = {
  TIENE_REFERENCIA: '#3b82f640',
  TIENE_CITA:       '#f59e0b35',
  CITA_A:           '#10b98155',
  ESCRITO_POR:      '#a855f735',
}

const RADIO = { Documento: 11, Referencia: 8, Cita: 5, Autor: 4 }

function colorNodo(node) {
  if (node.tipo === 'Referencia')
    return COLOR_REFERENCIA[node.nivel_confianza] ?? COLOR_REFERENCIA._pendiente
  return COLOR_NODO[node.tipo] ?? '#6b7280'
}

// ── Leyenda ──────────────────────────────────────────────────────────────────

const LEYENDA = [
  { label: 'Documento',          color: '#3b82f6' },
  { label: 'Ref. verificada',    color: '#10b981' },
  { label: 'Ref. abstract',      color: '#06b6d4' },
  { label: 'Ref. no encontrada', color: '#ef4444' },
  { label: 'Ref. pendiente',     color: '#6b7280' },
  { label: 'Cita',               color: '#f59e0b' },
  { label: 'Autor',              color: '#a855f7' },
]

// ── Componente ───────────────────────────────────────────────────────────────

const ALTURA = 580

export default function GrafoVisual({ data }) {
  const containerRef = useRef()
  const [ancho, setAncho]               = useState(0)
  const [nodoSel, setNodoSel]           = useState(null)
  const [mostrarCitas, setMostrarCitas] = useState(true)
  const [mostrarAutores, setMostrarAutores] = useState(false)

  // Medir ancho real del contenedor
  useEffect(() => {
    if (!containerRef.current) return
    const obs = new ResizeObserver(entries => setAncho(entries[0].contentRect.width))
    obs.observe(containerRef.current)
    return () => obs.disconnect()
  }, [])

  // Filtrar nodos/links según toggles
  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] }
    const excluir = new Set()
    if (!mostrarCitas)   excluir.add('Cita')
    if (!mostrarAutores) excluir.add('Autor')

    const ids = new Set(data.nodes.filter(n => !excluir.has(n.tipo)).map(n => n.id))
    return {
      nodes: data.nodes.filter(n => ids.has(n.id)),
      links: data.links.filter(l => {
        const src = typeof l.source === 'object' ? l.source.id : l.source
        const tgt = typeof l.target === 'object' ? l.target.id : l.target
        return ids.has(src) && ids.has(tgt)
      }),
    }
  }, [data, mostrarCitas, mostrarAutores])

  // Renderizado custom de cada nodo sobre canvas
  const paintNode = useCallback((node, ctx, globalScale) => {
    const r     = RADIO[node.tipo] ?? 5
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
    const r = (RADIO[node.tipo] ?? 5) + 3
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
          <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {stats.nodos} nodos · {stats.links} enlaces
          </span>
          <Toggle activo={mostrarCitas}    onChange={setMostrarCitas}    label="Citas" />
          <Toggle activo={mostrarAutores}  onChange={setMostrarAutores}  label="Autores" />
        </div>
      </div>

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

function DetalleNodo({ nodo }) {
  const filas = [
    nodo.tipo === 'Documento'  && ['Archivo',    nodo.label],
    nodo.tipo === 'Referencia' && ['Referencia', nodo.label],
    nodo.titulo                && ['Título',     nodo.titulo],
    nodo.nivel_confianza       && ['Cobertura',  nodo.nivel_confianza.replace('_', ' ')],
    nodo.tipo === 'Cita'       && ['Cita',       nodo.label],
    nodo.tipo_cita             && ['Tipo',       nodo.tipo_cita],
    nodo.tipo === 'Autor'      && ['Autor',      nodo.label],
  ].filter(Boolean)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
      {filas.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', gap: '0.75rem', fontSize: '0.8rem', lineHeight: 1.5 }}>
          <span style={{ color: 'var(--text-muted)', minWidth: '5.5rem', flexShrink: 0 }}>{k}</span>
          <span style={{ color: 'var(--text-primary)', wordBreak: 'break-word' }}>{v}</span>
        </div>
      ))}
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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import ForceGraph2D from 'react-force-graph-2d'
import { X } from 'lucide-react'
import BadgeVeredicto from '../components/BadgeVeredicto'
import TarjetaMetrica from '../components/TarjetaMetrica'
import { EstadoCarga, EstadoError } from '../components/Estados'
import { grafoAPI } from '../api/client'
import { pct } from '../lib/formato'

const COLORES_TIPO = {
  Documento: '#f59e0b',
  Referencia: '#3b82f6',
  Cita: '#10b981',
  Autor: '#8b5cf6',
}

// Filtros por veredicto (mismo código de color que el resto de la app).
const FILTROS_VEREDICTO = [
  { clave: 'SUPPORTS', texto: 'Respaldadas', color: '#15803d' },
  { clave: 'REFUTES', texto: 'Refutadas', color: '#b91c1c' },
  { clave: 'NO_INFO', texto: 'Sin evidencia', color: '#52525b' },
  { clave: 'SIN_AUDITAR', texto: 'Sin auditar', color: '#10b981' },
]

function DetalleNodo({ nodo, onCerrar }) {
  if (!nodo) return null
  return (
    <aside
      className="tarjeta tarjeta-pad"
      style={{ position: 'absolute', top: 14, right: 14, width: 'min(320px, calc(100% - 28px))', zIndex: 10, display: 'flex', flexDirection: 'column', gap: 9, maxHeight: '85%', overflowY: 'auto' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="badge badge-accent">{nodo.tipo}</span>
        <span style={{ flex: 1 }} />
        <button className="btn-icono" onClick={onCerrar}><X size={14} /></button>
      </div>
      <strong className="texto-doc">{nodo.label}</strong>
      {nodo.titulo && <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{nodo.titulo}</p>}
      {nodo.tipo === 'Referencia' && (
        <div style={{ fontSize: 13, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {nodo.autores?.length > 0 && <span><b>Autores:</b> {nodo.autores.join('; ')}</span>}
          {nodo.fuente && <span><b>Fuente:</b> {nodo.fuente}</span>}
          {nodo.doi && <span><b>DOI:</b> {nodo.doi}</span>}
          {nodo.nivel_confianza && <span><b>Verificación:</b> {nodo.nivel_confianza}</span>}
          {nodo.score_crossref != null && <span><b>Score CrossRef:</b> {nodo.score_crossref}</span>}
        </div>
      )}
      {nodo.tipo === 'Cita' && (
        <div style={{ fontSize: 13, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <BadgeVeredicto veredicto={nodo.veredicto} />
          {nodo.fragmento && <p className="texto-doc" style={{ color: 'var(--text-secondary)' }}>“{nodo.fragmento}”</p>}
          {nodo.justificacion && <span><b>Justificación:</b> {nodo.justificacion}</span>}
          {nodo.similitud != null && <span><b>Similitud:</b> {pct(nodo.similitud)}</span>}
          {nodo.pagina != null && <span><b>Página:</b> {nodo.pagina}</span>}
        </div>
      )}
    </aside>
  )
}

// Fase ④: grafo interactivo del documento (Neo4j → react-force-graph-2d).
export default function PaginaGrafo() {
  const { documentoId } = useOutletContext()
  const [datos, setDatos] = useState(null)
  const [resumen, setResumen] = useState(null)
  const [error, setError] = useState(null)
  const [nodoActivo, setNodoActivo] = useState(null)
  const contRef = useRef(null)
  const [ancho, setAncho] = useState(800)
  // Filtros de veredicto: las citas fuera del filtro se ocultan del grafo.
  const [filtros, setFiltros] = useState(new Set(['SUPPORTS', 'REFUTES', 'NO_INFO', 'SIN_AUDITAR']))

  const cargar = useCallback(async () => {
    setError(null)
    try {
      const [g, r] = await Promise.all([
        grafoAPI.verGrafoVisual(documentoId),
        grafoAPI.verResumen(documentoId).catch(() => null),
      ])
      setDatos(g.data)
      setResumen(r?.data || null)
    } catch (e) {
      setError(e)
    }
  }, [documentoId])

  useEffect(() => { cargar() }, [cargar])

  useEffect(() => {
    const medir = () => contRef.current && setAncho(contRef.current.clientWidth)
    medir()
    window.addEventListener('resize', medir)
    return () => window.removeEventListener('resize', medir)
  }, [datos])

  const conteos = useMemo(() => {
    const c = { SUPPORTS: 0, REFUTES: 0, NO_INFO: 0, SIN_AUDITAR: 0 }
    for (const n of datos?.nodes || []) {
      if (n.tipo === 'Cita') c[n.veredicto || 'SIN_AUDITAR'] += 1
    }
    return c
  }, [datos])

  const grafo = useMemo(() => {
    if (!datos) return null
    const visibles = new Set()
    const nodes = datos.nodes
      .filter((n) => {
        if (n.tipo !== 'Cita') return true
        return filtros.has(n.veredicto || 'SIN_AUDITAR')
      })
      .map((n) => {
        visibles.add(n.id)
        return { ...n }
      })
    const links = datos.links
      .filter((l) => visibles.has(l.source) && visibles.has(l.target))
      .map((l) => ({ ...l }))
    return { nodes, links }
  }, [datos, filtros])

  const alternarFiltro = (clave) => {
    setFiltros((prev) => {
      const s = new Set(prev)
      if (s.has(clave)) s.delete(clave)
      else s.add(clave)
      return s
    })
    setNodoActivo(null)
  }

  if (error) return <EstadoError error={error} onReintentar={cargar} />
  if (!grafo) return <EstadoCarga mensaje="Cargando grafo…" />

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <header>
        <h2>Grafo de conocimiento</h2>
        <p style={{ color: 'var(--text-secondary)', marginTop: 4, fontSize: 14 }}>
          Documento, citas, referencias y autores conectados. Haz clic en un nodo para ver su detalle.
        </p>
      </header>

      {resumen && (
        <div className="grid-metricas">
          <TarjetaMetrica etiqueta="Nodos" valor={resumen.total_nodos} />
          <TarjetaMetrica etiqueta="Relaciones" valor={resumen.total_relaciones} />
          <TarjetaMetrica etiqueta="Citas vinculadas" valor={resumen.citas_vinculadas} />
          <TarjetaMetrica
            etiqueta="Densidad"
            valor={resumen.densidad_promedio}
            detalle={resumen.grafo_robusto ? 'grafo robusto' : 'por debajo del mínimo recomendado'}
            color={resumen.grafo_robusto ? 'var(--supports)' : 'var(--warn)'}
          />
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Mostrar citas:</span>
        {FILTROS_VEREDICTO.map(({ clave, texto, color }) => {
          const activa = filtros.has(clave)
          return (
            <button
              key={clave}
              onClick={() => alternarFiltro(clave)}
              className="badge"
              style={{
                cursor: 'pointer',
                border: `1.5px solid ${activa ? color : 'var(--border)'}`,
                background: activa ? undefined : 'transparent',
                backgroundColor: activa ? `color-mix(in srgb, ${color} 14%, transparent)` : 'transparent',
                color: activa ? color : 'var(--text-faint)',
                opacity: activa ? 1 : 0.7,
              }}
              title={activa ? 'Clic para ocultar' : 'Clic para mostrar'}
            >
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: activa ? color : 'var(--text-faint)' }} />
              {texto} ({conteos[clave]})
            </button>
          )
        })}
      </div>

      <div ref={contRef} className="tarjeta" style={{ position: 'relative', height: 560, overflow: 'hidden' }}>
        <ForceGraph2D
          graphData={grafo}
          width={ancho}
          height={560}
          nodeLabel={(n) =>
            n.tipo === 'Cita' && n.similitud != null
              ? `${n.label} · similitud ${pct(n.similitud)}`
              : n.label
          }
          nodeColor={(n) => {
            if (n.tipo === 'Cita' && n.veredicto) {
              return n.veredicto === 'SUPPORTS' ? '#15803d' : n.veredicto === 'REFUTES' ? '#b91c1c' : '#52525b'
            }
            return COLORES_TIPO[n.tipo] || '#94a3b8'
          }}
          nodeVal={(n) => {
            if (n.tipo === 'Documento') return 9
            if (n.tipo === 'Referencia') return 5
            // Citas: el tamaño refleja la similitud con la evidencia (0-100%).
            if (n.tipo === 'Cita') return 2 + (n.similitud || 0) * 12
            return 2.5 // Autor
          }}
          linkColor={() => 'rgba(128,140,155,0.35)'}
          onNodeClick={setNodoActivo}
          cooldownTicks={90}
        />
        <DetalleNodo nodo={nodoActivo} onCerrar={() => setNodoActivo(null)} />
        <div style={{ position: 'absolute', left: 14, bottom: 12, display: 'flex', gap: 12, fontSize: 12, color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
          {Object.entries(COLORES_TIPO).map(([tipo, color]) => (
            <span key={tipo} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: color }} />
              {tipo}
            </span>
          ))}
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--text-faint)' }} />
            <span style={{ width: 11, height: 11, borderRadius: '50%', background: 'var(--text-faint)' }} />
            tamaño de cita = % de similitud
          </span>
        </div>
      </div>
    </div>
  )
}

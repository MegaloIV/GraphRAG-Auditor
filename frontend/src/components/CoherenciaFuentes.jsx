import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { Network, X, RefreshCcw } from 'lucide-react'
import { EstadoCarga, ErrorInline } from './Estados'
import { coherenciaAPI } from '../api/client'

// Coherencia inter-fuentes (docs/COHERENCIA.md §9): mapa de referencias
// conectadas por lo que DICEN sus evidencias (no por la estructura del
// documento). Las evidencias viven en el panel de detalle, no en el lienzo.

const COLORES_RELACION = {
  APOYA: '#15803d',
  CONTRADICE: '#b91c1c',
  COMPLEMENTA: '#3b82f6',
  CO_MENCION: 'rgba(128,140,155,0.5)',
}

const FILTROS_RELACION = [
  { clave: 'APOYA', texto: 'Apoyan', color: '#15803d' },
  { clave: 'CONTRADICE', texto: 'Contradicen', color: '#b91c1c' },
  { clave: 'COMPLEMENTA', texto: 'Complementan', color: '#3b82f6' },
  { clave: 'CO_MENCION', texto: 'Co-mención', color: '#64748b' },
]

const BADGE_HALLAZGO = {
  contradiccion_interna: { texto: 'Contradicción interna', clase: 'badge-refutes' },
  triangulacion: { texto: 'Triangulación', clase: 'badge-supports' },
  fuente_isla: { texto: 'Fuente isla', clase: 'badge-warn' },
  concepto_debil: { texto: 'Concepto débil', clase: 'badge-warn' },
}

function PanelDetalle({ seleccion, nodosPorId, onCerrar }) {
  if (!seleccion) return null
  const esArista = seleccion.tipo === 'arista'
  const d = seleccion.data
  return (
    <aside
      className="tarjeta tarjeta-pad"
      style={{ position: 'absolute', top: 14, right: 14, width: 'min(340px, calc(100% - 28px))', zIndex: 10, display: 'flex', flexDirection: 'column', gap: 10, maxHeight: '88%', overflowY: 'auto' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {esArista ? (
          <span className="badge" style={{ background: `color-mix(in srgb, ${COLORES_RELACION[d.tipo]} 15%, transparent)`, color: COLORES_RELACION[d.tipo] }}>
            {FILTROS_RELACION.find((f) => f.clave === d.tipo)?.texto || d.tipo}
          </span>
        ) : (
          <span className="badge badge-accent">Referencia</span>
        )}
        <span style={{ flex: 1 }} />
        <button className="btn-icono" onClick={onCerrar}><X size={14} /></button>
      </div>

      {esArista ? (
        <>
          <strong style={{ fontSize: 13.5 }}>
            {nodosPorId[typeof d.source === 'object' ? d.source.id : d.source]?.titulo}
            <span style={{ color: 'var(--text-faint)' }}> ↔ </span>
            {nodosPorId[typeof d.target === 'object' ? d.target.id : d.target]?.titulo}
          </strong>
          {d.tipo === 'CO_MENCION' ? (
            <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Comparten {d.pares} concepto(s), pero el juez no encontró una relación
              directa entre sus evidencias.
            </p>
          ) : (
            (d.detalles || []).map((det, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 6, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                {det.concepto && (
                  <span className="badge badge-neutro" style={{ alignSelf: 'flex-start' }}>{det.concepto}</span>
                )}
                <p className="texto-doc" style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>“{det.evidencia_1}”</p>
                <p className="texto-doc" style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>“{det.evidencia_2}”</p>
                <p style={{ fontSize: 13 }}>
                  <b>Juez ({det.confianza}):</b> {det.justificacion}
                </p>
              </div>
            ))
          )}
        </>
      ) : (
        <>
          <strong className="texto-doc">{d.titulo}{d.anio ? ` (${d.anio})` : ''}</strong>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            {d.n_citas} cita(s) del documento dependen de esta fuente.
          </p>
          {d.es_isla && (
            <p className="aviso-warn" style={{ fontSize: 12.5 }}>
              Fuente isla: sus evidencias no comparten conceptos con las demás fuentes.
            </p>
          )}
        </>
      )}
    </aside>
  )
}

export default function CoherenciaFuentes({ documentoId, auditada }) {
  const [datos, setDatos] = useState(null)
  const [cargando, setCargando] = useState(true)
  const [analizando, setAnalizando] = useState(false)
  const [error, setError] = useState(null)
  const [filtros, setFiltros] = useState(new Set(['APOYA', 'CONTRADICE', 'COMPLEMENTA', 'CO_MENCION']))
  const [conceptoActivo, setConceptoActivo] = useState(null)
  const [seleccion, setSeleccion] = useState(null)
  const fgRef = useRef(null)
  const contRef = useRef(null)
  const [ancho, setAncho] = useState(800)

  useEffect(() => {
    coherenciaAPI.ver(documentoId)
      .then((res) => setDatos(res.data))
      .catch(() => {}) // 404 = aún no construido; se muestra el botón
      .finally(() => setCargando(false))
  }, [documentoId])

  useEffect(() => {
    const medir = () => contRef.current && setAncho(contRef.current.clientWidth)
    medir()
    window.addEventListener('resize', medir)
    return () => window.removeEventListener('resize', medir)
  }, [datos])

  const analizar = async () => {
    setAnalizando(true)
    setError(null)
    try {
      const { data } = await coherenciaAPI.analizar(documentoId)
      setDatos(data)
      setSeleccion(null)
    } catch (e) {
      setError(e)
    } finally {
      setAnalizando(false)
    }
  }

  const nodosPorId = useMemo(() => {
    const mapa = {}
    for (const n of datos?.mapa?.nodes || []) mapa[n.id] = n
    return mapa
  }, [datos])

  const grafo = useMemo(() => {
    if (!datos?.mapa) return null
    const refsConcepto = conceptoActivo
      ? new Set(datos.mapa.conceptos.find((c) => c.id === conceptoActivo)?.referencia_ids || [])
      : null
    const nodes = datos.mapa.nodes
      .filter((n) => !refsConcepto || refsConcepto.has(n.id))
      .map((n) => ({ ...n }))
    const visibles = new Set(nodes.map((n) => n.id))
    const links = datos.mapa.links
      .filter((l) => filtros.has(l.tipo) && visibles.has(l.source) && visibles.has(l.target))
      .map((l) => ({ ...l }))
    return { nodes, links }
  }, [datos, filtros, conceptoActivo])

  const conteosRelacion = useMemo(() => {
    const c = { APOYA: 0, CONTRADICE: 0, COMPLEMENTA: 0, CO_MENCION: 0 }
    for (const l of datos?.mapa?.links || []) c[l.tipo] = (c[l.tipo] || 0) + 1
    return c
  }, [datos])

  const alternarFiltro = (clave) => {
    setFiltros((prev) => {
      const s = new Set(prev)
      if (s.has(clave)) s.delete(clave)
      else s.add(clave)
      return s
    })
    setSeleccion(null)
  }

  // Clic en una cartilla de hallazgo → zoom sobre las referencias involucradas
  // (mismo gesto que "clic en cartilla → el PDF salta a la cita" en Revisión).
  const enfocarHallazgo = useCallback((h) => {
    if (!h.referencia_ids?.length) return
    setConceptoActivo(null)
    setSeleccion(null)
    setTimeout(() => {
      fgRef.current?.zoomToFit(700, 70, (n) => h.referencia_ids.includes(n.id))
    }, 120)
  }, [])

  if (cargando) return <EstadoCarga mensaje="Consultando análisis de coherencia…" />

  const construido = datos && datos.total_evidencias > 0

  return (
    <section className="tarjeta tarjeta-pad" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 240 }}>
          <h3 style={{ fontSize: 15, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Network size={16} color="var(--accent)" />
            Coherencia entre fuentes
          </h3>
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
            ¿Los papers que sustentan la tesis se apoyan, se contradicen o ni se
            relacionan entre sí? Compara las evidencias de los papers, no las
            afirmaciones del tesista.
          </p>
        </div>
        <button className="btn btn-contorno" onClick={analizar} disabled={analizando || !auditada}
          title={!auditada ? 'Ejecuta primero la auditoría.' : undefined}>
          <RefreshCcw size={14} className={analizando ? 'girando' : ''} />
          {analizando ? 'Analizando…' : construido ? 'Re-analizar' : 'Analizar coherencia'}
        </button>
      </div>

      <ErrorInline error={error} />

      {analizando && (
        <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          Extrayendo conceptos y comparando evidencias entre papers — esto puede
          tardar unos minutos…
        </p>
      )}

      {construido && datos.mapa.nodes.length < 2 && (
        <div className="aviso-warn">
          Se necesitan al menos 2 referencias con evidencia auditada para comparar fuentes.
        </div>
      )}

      {construido && datos.mapa.nodes.length >= 2 && (
        <>
          <div className="grid-metricas">
            <div className="tarjeta-metrica"><div className="valor">{datos.total_evidencias}</div><div className="etiqueta">Evidencias</div></div>
            <div className="tarjeta-metrica"><div className="valor">{datos.total_conceptos}</div><div className="etiqueta">Conceptos</div></div>
            <div className="tarjeta-metrica"><div className="valor">{datos.total_comparaciones}</div><div className="etiqueta">Comparaciones</div></div>
            <div className="tarjeta-metrica"><div className="valor">{datos.total_relaciones}</div><div className="etiqueta">Relaciones</div></div>
          </div>

          {/* Filtros por tipo de relación */}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Relaciones:</span>
            {FILTROS_RELACION.map(({ clave, texto, color }) => {
              const activa = filtros.has(clave)
              return (
                <button key={clave} onClick={() => alternarFiltro(clave)} className="badge"
                  style={{
                    cursor: 'pointer',
                    border: `1.5px solid ${activa ? color : 'var(--border)'}`,
                    backgroundColor: activa ? `color-mix(in srgb, ${color} 14%, transparent)` : 'transparent',
                    color: activa ? color : 'var(--text-faint)',
                  }}>
                  {texto} ({conteosRelacion[clave] || 0})
                </button>
              )
            })}
          </div>

          {/* Filtro por concepto */}
          {datos.mapa.conceptos.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Concepto:</span>
              <button className="badge" onClick={() => setConceptoActivo(null)}
                style={{ cursor: 'pointer', border: `1.5px solid ${!conceptoActivo ? 'var(--accent)' : 'var(--border)'}`, color: !conceptoActivo ? 'var(--accent)' : 'var(--text-faint)', background: 'transparent' }}>
                Todos
              </button>
              {datos.mapa.conceptos.slice(0, 12).map((c) => {
                const activo = conceptoActivo === c.id
                return (
                  <button key={c.id} className="badge" onClick={() => setConceptoActivo(activo ? null : c.id)}
                    style={{ cursor: 'pointer', border: `1.5px solid ${activo ? 'var(--accent)' : 'var(--border)'}`, color: activo ? 'var(--accent)' : 'var(--text-secondary)', background: activo ? 'var(--accent-subtle)' : 'transparent' }}>
                    {c.nombre} ({c.n_referencias})
                  </button>
                )
              })}
            </div>
          )}

          {/* Mapa de referencias */}
          <div ref={contRef} style={{ position: 'relative', height: 440, overflow: 'hidden', background: 'var(--bg-sunken)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)' }}>
            <ForceGraph2D
              ref={fgRef}
              graphData={grafo}
              width={ancho}
              height={440}
              nodeLabel={(n) => `${n.label} · ${n.n_citas} cita(s)`}
              nodeColor={(n) => (n.es_isla ? '#b45309' : '#64748b')}
              nodeVal={(n) => 3 + Math.min(n.n_citas, 10)}
              linkColor={(l) => COLORES_RELACION[l.tipo]}
              linkWidth={(l) => (l.tipo === 'CO_MENCION' ? 1 : 1.5 + Math.min(l.pares, 4))}
              linkLineDash={(l) => (l.tipo === 'CO_MENCION' ? [4, 4] : null)}
              linkLabel={(l) => `${l.tipo === 'CO_MENCION' ? 'Co-mención' : l.tipo} · ${l.pares} par(es)`}
              onLinkClick={(l) => setSeleccion({ tipo: 'arista', data: l })}
              onNodeClick={(n) => setSeleccion({ tipo: 'nodo', data: n })}
              cooldownTicks={90}
            />
            <PanelDetalle seleccion={seleccion} nodosPorId={nodosPorId} onCerrar={() => setSeleccion(null)} />
            <div style={{ position: 'absolute', left: 12, bottom: 10, display: 'flex', gap: 12, fontSize: 12, color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
              {FILTROS_RELACION.map(({ clave, texto, color }) => (
                <span key={clave} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                  <span style={{ width: 14, height: 2.5, background: color, borderRadius: 2 }} />
                  {texto}
                </span>
              ))}
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                <span style={{ width: 9, height: 9, borderRadius: '50%', background: '#b45309' }} />
                fuente isla
              </span>
            </div>
          </div>

          {/* Cartillas de hallazgos */}
          {datos.hallazgos.length === 0 ? (
            <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Sin hallazgos: no se detectaron contradicciones ni fuentes aisladas entre las evidencias.
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <h4 style={{ fontSize: 13.5 }}>Hallazgos ({datos.hallazgos.length})</h4>
              {datos.hallazgos.map((h, i) => {
                const badge = BADGE_HALLAZGO[h.tipo] || { texto: h.tipo, clase: 'badge-neutro' }
                return (
                  <div key={i} className="cartilla" style={{ padding: 12, gap: 8, cursor: 'pointer' }}
                    onClick={() => enfocarHallazgo(h)}
                    title="Clic para enfocar en el mapa">
                    <div className="cartilla-cabecera">
                      <span className={`badge ${badge.clase}`}>{badge.texto}</span>
                      {h.concepto && <span className="badge badge-neutro">{h.concepto}</span>}
                      {h.confianza && <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>confianza {h.confianza}</span>}
                    </div>
                    <p style={{ fontSize: 13.5 }}>{h.justificacion}</p>
                    {h.referencias.length > 0 && (
                      <p style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
                        Fuentes: {h.referencias.join(' · ')}
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </section>
  )
}

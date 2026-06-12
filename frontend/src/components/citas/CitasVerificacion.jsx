import { useState, useMemo } from 'react'
import { ingestaAPI } from '../../api/client'

// ── Helpers ──────────────────────────────────────────────────────────────────

function estadoCita(cita, refPorId) {
  if (!cita.referencia_id) return 'sin_referencia'
  const ref = refPorId[cita.referencia_id]
  if (!ref) return 'sin_referencia'
  if (ref.nivel_confianza === 'no_encontrado') return 'no_encontrado'
  if (ref.nivel_confianza) return 'encontrado'
  return 'pendiente'
}

const ETIQUETA_CONFIANZA = {
  texto_completo: { label: 'Texto completo',    color: 'var(--success)' },
  abstract:       { label: 'Abstract',           color: 'var(--accent)'  },
  cache:          { label: 'Caché',              color: 'var(--accent)'  },
  manual:         { label: 'Subido manualmente', color: '#8b5cf6'        },
}

// ── Componente principal ─────────────────────────────────────────────────────

export default function CitasVerificacion({
  documentoId,
  citas,
  referencias,
  estadoPipeline,
  onVerificacionIniciada,
}) {
  const refPorId = useMemo(() => {
    const m = {}
    referencias?.forEach(r => { m[r.referencia_id] = r })
    return m
  }, [referencias])

  const pendientes = useMemo(
    () => citas.filter(c => estadoCita(c, refPorId) === 'pendiente'),
    [citas, refPorId]
  )
  const pendientesRefIds = useMemo(
    () => new Set(pendientes.map(c => c.referencia_id).filter(Boolean)),
    [pendientes]
  )
  const [seleccionadas, setSeleccionadas] = useState(() => new Set(pendientesRefIds))
  const [enviando, setEnviando] = useState(false)
  const [errorEnvio, setErrorEnvio] = useState(null)

  const toggleCita = (id) =>
    setSeleccionadas(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const seleccionarTodas   = () => setSeleccionadas(new Set(pendientesRefIds))
  const deseleccionarTodas = () => setSeleccionadas(new Set())

  const handleVerificar = async () => {
    if (seleccionadas.size === 0) return
    setEnviando(true)
    setErrorEnvio(null)
    try {
      await ingestaAPI.iniciarVerificacion(documentoId, [...seleccionadas])
      onVerificacionIniciada()
    } catch (err) {
      setErrorEnvio(err.mensaje || 'Error al iniciar la verificación.')
      setEnviando(false)
    }
  }

  const citasOrdenadas = useMemo(() => {
    const orden = { pendiente: 0, no_encontrado: 1, encontrado: 2, sin_referencia: 3 }
    return [...citas].sort((a, b) => orden[estadoCita(a, refPorId)] - orden[estadoCita(b, refPorId)])
  }, [citas, refPorId])

  const verificando = estadoPipeline === 'verificando'

  return (
    <div>
      {/* Banner verificando */}
      {verificando && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.75rem',
          padding: '0.875rem 1rem', marginBottom: '1rem',
          background: 'var(--accent-subtle)',
          border: '1px solid rgba(59,130,246,0.2)',
          borderRadius: 'var(--radius-md)',
          animation: 'fadeIn 0.3s ease forwards',
        }}>
          <Spinner color="var(--accent)" />
          <span style={{ fontSize: '0.85rem', color: 'var(--accent)', fontWeight: 600 }}>
            Verificando referencias en CrossRef y Unpaywall...
          </span>
        </div>
      )}

      {/* Barra de acciones */}
      {pendientesRefIds.size > 0 && !verificando && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1rem',
          padding: '0.75rem 1rem',
          background: 'var(--bg-surface-2)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              {seleccionadas.size} de {pendientesRefIds.size} referencia(s) pendiente(s)
            </span>
            <BtnTexto onClick={seleccionarTodas}>Todas</BtnTexto>
            <BtnTexto onClick={deseleccionarTodas}>Ninguna</BtnTexto>
          </div>
          <button
            onClick={handleVerificar}
            disabled={seleccionadas.size === 0 || enviando}
            style={{
              padding: '0.5rem 1.25rem',
              background: seleccionadas.size === 0 || enviando ? 'var(--bg-surface)' : 'var(--accent)',
              color: seleccionadas.size === 0 || enviando ? 'var(--text-muted)' : 'white',
              border: 'none', borderRadius: 'var(--radius-sm)',
              fontSize: '0.82rem', fontWeight: 600,
              cursor: seleccionadas.size === 0 || enviando ? 'not-allowed' : 'pointer',
              fontFamily: 'var(--font-sans)',
              display: 'flex', alignItems: 'center', gap: '0.4rem',
            }}
          >
            {enviando
              ? <><Spinner color="white" size={14} /> Enviando...</>
              : `Verificar ${seleccionadas.size} referencia(s)`}
          </button>
        </div>
      )}

      {errorEnvio && (
        <div style={{
          marginBottom: '0.75rem', padding: '0.75rem 1rem',
          background: 'var(--error-subtle)', border: '1px solid rgba(239,68,68,0.2)',
          borderRadius: 'var(--radius-md)', fontSize: '0.82rem', color: 'var(--error)',
        }}>
          ✕ {errorEnvio}
        </div>
      )}

      {/* Lista de citas */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
        {citasOrdenadas.map((cita, i) => {
          const estado = estadoCita(cita, refPorId)
          const ref    = refPorId[cita.referencia_id]
          const marcada = seleccionadas.has(cita.referencia_id)

          return (
            <div
              key={cita.cita_id}
              style={{
                borderRadius: 'var(--radius-md)',
                border: `1px solid ${
                  estado === 'pendiente' && marcada ? 'rgba(59,130,246,0.35)' :
                  estado === 'encontrado'           ? 'rgba(16,185,129,0.2)' :
                  estado === 'no_encontrado'        ? 'rgba(239,68,68,0.2)' :
                  'transparent'
                }`,
                background: estado === 'pendiente' && marcada
                  ? 'var(--accent-subtle)'
                  : 'var(--bg-surface-2)',
                overflow: 'hidden',
                transition: 'border-color 0.15s, background 0.15s',
                animation: `fadeIn 0.3s ease ${i * 0.02}s forwards`,
                opacity: 0,
              }}
            >
              <div
                onClick={() =>
                  estado === 'pendiente' && !verificando && cita.referencia_id &&
                  toggleCita(cita.referencia_id)
                }
                style={{
                  padding: '0.875rem',
                  display: 'flex', gap: '0.75rem', alignItems: 'flex-start',
                  cursor: estado === 'pendiente' && !verificando ? 'pointer' : 'default',
                }}
              >
                <EstadoIcono
                  estado={estado}
                  marcada={marcada}
                  verificando={verificando && estado === 'pendiente'}
                />

                <div style={{ flex: 1, minWidth: 0 }}>
                  {/* Texto de la cita */}
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: '0.84rem',
                    color: 'var(--accent)', fontWeight: 600, marginBottom: '0.35rem',
                  }}>
                    {cita.texto_cita}
                    <span style={{
                      marginLeft: '0.6rem', fontSize: '0.7rem', fontFamily: 'var(--font-sans)',
                      color: 'var(--text-muted)', fontWeight: 400,
                    }}>
                      {cita.tipo === 'parentetica' ? 'Parentética' : 'Narrativa'} · p.{cita.pagina}
                    </span>
                  </div>

                  {/* Fragmento */}
                  {cita.fragmento_oracion && (
                    <div style={{
                      fontSize: '0.78rem', color: 'var(--text-secondary)', fontStyle: 'italic',
                      lineHeight: 1.6, borderLeft: '2px solid var(--border)',
                      paddingLeft: '0.6rem', marginBottom: '0.5rem',
                    }}>
                      "...{cita.fragmento_oracion}..."
                    </div>
                  )}

                  {/* Referencia vinculada */}
                  {ref ? (
                    <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', marginBottom: '0.4rem' }}>
                      <span style={{ color: 'var(--text-secondary)' }}>↳</span>{' '}
                      {ref.autores?.[0] ?? 'Sin autor'}{ref.autores?.length > 1 ? ' et al.' : ''}
                      {ref.anio ? ` (${ref.anio})` : ''}
                      {ref.titulo ? ` — ${ref.titulo.slice(0, 70)}${ref.titulo.length > 70 ? '…' : ''}` : ''}
                    </div>
                  ) : (
                    <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                      Sin referencia vinculada
                    </div>
                  )}

                  <FooterCard estado={estado} ref_={ref} verificando={verificando} />
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {citas.length === 0 && (
        <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          No se detectaron citas en este documento.
        </div>
      )}
    </div>
  )
}

// ── Sub-componentes ──────────────────────────────────────────────────────────

function EstadoIcono({ estado, marcada, verificando }) {
  if (estado === 'pendiente') {
    if (verificando) {
      return (
        <div style={{ width: 20, height: 20, flexShrink: 0, marginTop: 2, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Spinner color="var(--accent)" size={14} />
        </div>
      )
    }
    return (
      <div style={{
        width: 18, height: 18, borderRadius: 4, flexShrink: 0, marginTop: 2,
        border: `2px solid ${marcada ? 'var(--accent)' : 'var(--border)'}`,
        background: marcada ? 'var(--accent)' : 'transparent',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'all 0.15s',
      }}>
        {marcada && (
          <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
            <path d="M1 4L3.5 6.5L9 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </div>
    )
  }
  if (estado === 'encontrado') {
    return (
      <div style={{
        width: 20, height: 20, borderRadius: '50%', flexShrink: 0, marginTop: 2,
        background: 'var(--success)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: '0.65rem', color: 'white',
      }}>✓</div>
    )
  }
  if (estado === 'no_encontrado') {
    return (
      <div style={{
        width: 20, height: 20, borderRadius: '50%', flexShrink: 0, marginTop: 2,
        background: 'var(--error)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: '0.65rem', color: 'white',
      }}>✕</div>
    )
  }
  return (
    <div style={{
      width: 20, height: 20, borderRadius: '50%', flexShrink: 0, marginTop: 2,
      background: 'var(--bg-surface)', border: '1px solid var(--border)',
    }} />
  )
}

function FooterCard({ estado, ref_, verificando }) {
  if (estado === 'sin_referencia') {
    return (
      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
        No se puede verificar — sin referencia vinculada
      </div>
    )
  }

  if (estado === 'pendiente') {
    return (
      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
        {verificando ? 'Buscando paper...' : 'Pendiente de verificación'}
      </div>
    )
  }

  if (estado === 'encontrado') {
    const info = ETIQUETA_CONFIANZA[ref_?.nivel_confianza] ?? { label: ref_?.nivel_confianza, color: 'var(--accent)' }
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.25rem', flexWrap: 'wrap' }}>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
          padding: '0.2rem 0.6rem', borderRadius: '999px',
          background: `${info.color}18`, border: `1px solid ${info.color}40`,
          fontSize: '0.7rem', color: info.color, fontWeight: 600,
        }}>
          ✓ {info.label}
        </span>
        {ref_?.doi && (
          <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            {ref_.doi}
          </span>
        )}
      </div>
    )
  }

  if (estado === 'no_encontrado') {
    return (
      <div style={{ marginTop: '0.35rem' }}>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
          padding: '0.2rem 0.6rem', borderRadius: '999px',
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)',
          fontSize: '0.7rem', color: 'var(--error)', fontWeight: 600,
        }}>
          ✕ No encontrado — adjunta el PDF en la pestaña Referencias
        </span>
      </div>
    )
  }

  return null
}

function BtnTexto({ onClick, children }) {
  return (
    <button onClick={onClick} style={{
      padding: '0.2rem 0.6rem', background: 'transparent',
      border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
      fontSize: '0.72rem', color: 'var(--text-secondary)',
      cursor: 'pointer', fontFamily: 'var(--font-sans)',
    }}>
      {children}
    </button>
  )
}

function Spinner({ color = 'var(--accent)', size = 16 }) {
  return (
    <div style={{
      width: size, height: size, flexShrink: 0,
      border: `2px solid ${color}30`,
      borderTop: `2px solid ${color}`,
      borderRadius: '50%',
      animation: 'spin 1s linear infinite',
    }} />
  )
}

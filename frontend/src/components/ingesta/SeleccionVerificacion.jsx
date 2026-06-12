import { useState, useMemo } from 'react'
import { ingestaAPI } from '../../api/client'

export default function SeleccionVerificacion({ documentoId, citas, referencias, onVerificacionIniciada }) {
  // Índice de referencias por id para lookup O(1)
  const refPorId = useMemo(() => {
    const mapa = {}
    referencias?.forEach(r => { mapa[r.referencia_id] = r })
    return mapa
  }, [referencias])

  // Sólo las citas que tienen referencia vinculada pueden verificarse
  const citasVerificables = useMemo(
    () => citas.filter(c => c.referencia_id),
    [citas]
  )
  const citasSinRef = useMemo(
    () => citas.filter(c => !c.referencia_id),
    [citas]
  )

  const [seleccionadas, setSeleccionadas] = useState(
    () => new Set(citasVerificables.map(c => c.cita_id))
  )
  const [verificando, setVerificando] = useState(false)
  const [error, setError] = useState(null)

  const toggleCita = (citaId) => {
    setSeleccionadas(prev => {
      const next = new Set(prev)
      next.has(citaId) ? next.delete(citaId) : next.add(citaId)
      return next
    })
  }

  const seleccionarTodas = () =>
    setSeleccionadas(new Set(citasVerificables.map(c => c.cita_id)))

  const deseleccionarTodas = () =>
    setSeleccionadas(new Set())

  const handleVerificar = async () => {
    if (seleccionadas.size === 0) return
    setVerificando(true)
    setError(null)
    try {
      await ingestaAPI.iniciarVerificacion(documentoId, [...seleccionadas])
      onVerificacionIniciada()
    } catch (err) {
      setError(err.mensaje || 'Error al iniciar la verificación.')
      setVerificando(false)
    }
  }

  return (
    <div style={{ animation: 'fadeIn 0.3s ease forwards' }}>

      {/* Header */}
      <div style={{
        padding: '1rem',
        background: 'var(--accent-subtle)',
        border: '1px solid rgba(59,130,246,0.2)',
        borderRadius: 'var(--radius-md)',
        marginBottom: '1.25rem',
      }}>
        <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--accent)', marginBottom: '0.25rem' }}>
          Extracción completada
        </div>
        <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          Se encontraron <strong>{citas.length}</strong> citas y{' '}
          <strong>{referencias?.length ?? 0}</strong> referencias.
          Selecciona las citas que quieres verificar con CrossRef y Unpaywall.
        </div>
      </div>

      {/* Controles de selección */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '0.75rem',
        flexWrap: 'wrap',
        gap: '0.5rem',
      }}>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          {seleccionadas.size} de {citasVerificables.length} citas seleccionadas
        </span>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <BtnTexto onClick={seleccionarTodas}>Seleccionar todas</BtnTexto>
          <BtnTexto onClick={deseleccionarTodas}>Ninguna</BtnTexto>
        </div>
      </div>

      {/* Lista de citas verificables */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        maxHeight: '460px',
        overflowY: 'auto',
        paddingRight: '0.25rem',
        marginBottom: '1rem',
      }}>
        {citasVerificables.map((cita, i) => {
          const marcada = seleccionadas.has(cita.cita_id)
          const ref = refPorId[cita.referencia_id]
          return (
            <div
              key={cita.cita_id}
              onClick={() => toggleCita(cita.cita_id)}
              style={{
                padding: '0.875rem',
                background: marcada ? 'var(--accent-subtle)' : 'var(--bg-surface-2)',
                border: `1px solid ${marcada ? 'rgba(59,130,246,0.3)' : 'transparent'}`,
                borderRadius: 'var(--radius-md)',
                cursor: 'pointer',
                display: 'flex',
                gap: '0.75rem',
                alignItems: 'flex-start',
                transition: 'background 0.15s ease, border-color 0.15s ease',
                animation: `fadeIn 0.3s ease ${i * 0.02}s forwards`,
                opacity: 0,
              }}
            >
              {/* Checkbox */}
              <div style={{
                width: '18px',
                height: '18px',
                borderRadius: '4px',
                border: `2px solid ${marcada ? 'var(--accent)' : 'var(--border)'}`,
                background: marcada ? 'var(--accent)' : 'transparent',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
                marginTop: '2px',
                transition: 'all 0.15s ease',
              }}>
                {marcada && (
                  <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
                    <path d="M1 4L3.5 6.5L9 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                {/* Cita APA */}
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.82rem',
                  color: 'var(--accent)',
                  fontWeight: 600,
                  marginBottom: '0.35rem',
                }}>
                  {cita.texto_cita}
                </div>

                {/* Fragmento completo */}
                {cita.fragmento_oracion && (
                  <div style={{
                    fontSize: '0.78rem',
                    color: 'var(--text-secondary)',
                    fontStyle: 'italic',
                    lineHeight: 1.6,
                    borderLeft: '2px solid var(--border)',
                    paddingLeft: '0.6rem',
                    marginBottom: '0.5rem',
                  }}>
                    "...{cita.fragmento_oracion}..."
                  </div>
                )}

                {/* Referencia vinculada */}
                {ref && (
                  <div style={{
                    fontSize: '0.73rem',
                    color: 'var(--text-muted)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.3rem',
                  }}>
                    <span style={{ color: 'var(--success)', fontSize: '0.7rem' }}>↳</span>
                    <span>
                      {ref.autores?.[0] ?? 'Sin autor'}
                      {ref.autores?.length > 1 ? ' et al.' : ''}
                      {ref.anio ? ` (${ref.anio})` : ''}
                      {ref.titulo ? ` — ${ref.titulo.slice(0, 60)}${ref.titulo.length > 60 ? '…' : ''}` : ''}
                    </span>
                  </div>
                )}

                {/* Página */}
                <div style={{
                  marginTop: '0.35rem',
                  fontSize: '0.7rem',
                  color: 'var(--text-muted)',
                  fontFamily: 'var(--font-mono)',
                }}>
                  p. {cita.pagina}
                  <span style={{ marginLeft: '0.5rem', color: 'var(--border)' }}>·</span>
                  <span style={{ marginLeft: '0.5rem' }}>
                    {cita.tipo === 'parentetica' ? 'Parentética' : 'Narrativa'}
                  </span>
                </div>
              </div>
            </div>
          )
        })}

        {/* Citas sin referencia vinculada (deshabilitadas) */}
        {citasSinRef.length > 0 && (
          <>
            <div style={{
              fontSize: '0.73rem',
              color: 'var(--text-muted)',
              padding: '0.5rem 0.25rem',
              borderTop: '1px solid var(--border)',
              marginTop: '0.25rem',
            }}>
              {citasSinRef.length} cita(s) sin referencia vinculada — no pueden verificarse
            </div>
            {citasSinRef.map(cita => (
              <div
                key={cita.cita_id}
                style={{
                  padding: '0.875rem',
                  background: 'var(--bg-surface-2)',
                  border: '1px solid transparent',
                  borderRadius: 'var(--radius-md)',
                  display: 'flex',
                  gap: '0.75rem',
                  alignItems: 'flex-start',
                  opacity: 0.45,
                  cursor: 'not-allowed',
                }}
              >
                <div style={{
                  width: '18px',
                  height: '18px',
                  borderRadius: '4px',
                  border: '2px solid var(--border)',
                  flexShrink: 0,
                  marginTop: '2px',
                }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: '0.82rem',
                    color: 'var(--text-secondary)',
                    fontWeight: 600,
                    marginBottom: '0.3rem',
                  }}>
                    {cita.texto_cita}
                  </div>
                  {cita.fragmento_oracion && (
                    <div style={{
                      fontSize: '0.78rem',
                      color: 'var(--text-muted)',
                      fontStyle: 'italic',
                      lineHeight: 1.5,
                      borderLeft: '2px solid var(--border)',
                      paddingLeft: '0.6rem',
                    }}>
                      "...{cita.fragmento_oracion}..."
                    </div>
                  )}
                  <div style={{ marginTop: '0.35rem', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    Sin referencia vinculada
                  </div>
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Error */}
      {error && (
        <div style={{
          marginBottom: '0.75rem',
          padding: '0.75rem 1rem',
          background: 'var(--error-subtle)',
          border: '1px solid rgba(239,68,68,0.2)',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.83rem',
          color: 'var(--error)',
        }}>
          ✕ {error}
        </div>
      )}

      {/* Botón verificar */}
      <button
        onClick={handleVerificar}
        disabled={seleccionadas.size === 0 || verificando}
        style={{
          width: '100%',
          padding: '0.875rem',
          background: seleccionadas.size === 0 || verificando
            ? 'var(--bg-surface-2)'
            : 'var(--accent)',
          color: seleccionadas.size === 0 || verificando ? 'var(--text-muted)' : 'white',
          border: 'none',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.95rem',
          fontWeight: 600,
          cursor: seleccionadas.size === 0 || verificando ? 'not-allowed' : 'pointer',
          fontFamily: 'var(--font-sans)',
          transition: 'background 0.2s ease',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '0.5rem',
        }}
      >
        {verificando ? (
          <>
            <Spinner />
            Iniciando verificación...
          </>
        ) : (
          `Verificar ${seleccionadas.size} cita${seleccionadas.size !== 1 ? 's' : ''} seleccionada${seleccionadas.size !== 1 ? 's' : ''}`
        )}
      </button>

      {seleccionadas.size === 0 && !verificando && (
        <div style={{
          marginTop: '0.5rem',
          fontSize: '0.75rem',
          color: 'var(--text-muted)',
          textAlign: 'center',
        }}>
          Selecciona al menos una cita para continuar
        </div>
      )}
    </div>
  )
}

function BtnTexto({ onClick, children }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '0.3rem 0.75rem',
        background: 'transparent',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-sm)',
        fontSize: '0.75rem',
        color: 'var(--text-secondary)',
        cursor: 'pointer',
        fontFamily: 'var(--font-sans)',
      }}
    >
      {children}
    </button>
  )
}

function Spinner() {
  return (
    <div style={{
      width: '16px',
      height: '16px',
      border: '2px solid rgba(255,255,255,0.3)',
      borderTop: '2px solid white',
      borderRadius: '50%',
      animation: 'spin 1s linear infinite',
    }} />
  )
}

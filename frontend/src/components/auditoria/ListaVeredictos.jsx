import { useState } from 'react'
import Badge from '../ui/Badge'

const VARIANTE_VEREDICTO = {
  'VÁLIDA':         { variante: 'success', icono: '✓' },
  'DUDOSA':         { variante: 'warning', icono: '~' },
  'ALUCINADA':      { variante: 'error',   icono: '✕' },
  'NO_VERIFICABLE': { variante: 'neutral', icono: '?' },
}

const FILTROS = [
  { id: 'todos',          label: 'Todos' },
  { id: 'VÁLIDA',         label: 'Válidas' },
  { id: 'DUDOSA',         label: 'Dudosas' },
  { id: 'ALUCINADA',      label: 'Alucinadas' },
  { id: 'NO_VERIFICABLE', label: 'No verificables' },
]

const RAGAS_BADGES = [
  { key: 'faithfulness',       sigla: 'F'  },
  { key: 'answer_relevancy',   sigla: 'AR' },
  { key: 'context_precision',  sigla: 'CP' },
  { key: 'context_recall',     sigla: 'CR' },
  { key: 'answer_correctness', sigla: 'AC' },
]

function colorRagas(valor) {
  if (valor == null) return 'var(--text-muted)'
  if (valor >= 0.80) return 'var(--success)'
  if (valor >= 0.60) return 'var(--warning)'
  return 'var(--error)'
}

function bgRagas(valor) {
  if (valor == null) return 'var(--bg-surface)'
  if (valor >= 0.80) return 'var(--success-subtle)'
  if (valor >= 0.60) return 'var(--warning-subtle)'
  return 'var(--error-subtle)'
}

export default function ListaVeredictos({
  veredictos,
  validas,
  dudosas,
  alucinadas,
  no_verificables,
  advertencia,
}) {
  const [filtro, setFiltro] = useState('todos')
  const [expandido, setExpandido] = useState(null)

  const citasFiltradas = filtro === 'todos'
    ? veredictos
    : veredictos.filter(v => v.veredicto === filtro)

  return (
    <div style={{ animation: 'fadeIn 0.3s ease forwards' }}>

      {/* Resumen */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: '0.6rem',
        marginBottom: '1.25rem',
      }}>
        <Chip label="Válidas"         valor={validas}         variante="success" />
        <Chip label="Dudosas"         valor={dudosas}         variante="warning" />
        <Chip label="Alucinadas"      valor={alucinadas}      variante="error"   />
        <Chip label="No verificables" valor={no_verificables} variante="neutral" />
      </div>

      {/* Advertencia */}
      {advertencia && (
        <div style={{
          padding: '0.75rem 1rem',
          background: 'var(--warning-subtle)',
          border: '1px solid rgba(245,158,11,0.2)',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.82rem',
          color: 'var(--warning)',
          marginBottom: '1rem',
          display: 'flex',
          gap: '0.5rem',
        }}>
          <span>⚠️</span><span>{advertencia}</span>
        </div>
      )}

      {/* Filtros */}
      <div style={{
        display: 'flex',
        gap: '0.25rem',
        marginBottom: '1rem',
        flexWrap: 'wrap',
      }}>
        {FILTROS.map(f => (
          <button
            key={f.id}
            onClick={() => setFiltro(f.id)}
            style={{
              padding: '0.35rem 0.875rem',
              borderRadius: '999px',
              border: '1px solid',
              borderColor: filtro === f.id ? 'var(--accent)' : 'var(--border)',
              background: filtro === f.id ? 'var(--accent-subtle)' : 'transparent',
              color: filtro === f.id ? 'var(--accent)' : 'var(--text-secondary)',
              fontSize: '0.78rem',
              fontWeight: filtro === f.id ? 600 : 400,
              cursor: 'pointer',
              fontFamily: 'var(--font-sans)',
              transition: 'all 0.15s ease',
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Lista */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        overflowY: 'auto',
        paddingRight: '0.25rem',
      }}>
        {citasFiltradas.length === 0 ? (
          <div style={{
            textAlign: 'center',
            padding: '2rem',
            color: 'var(--text-muted)',
            fontSize: '0.85rem',
          }}>
            No hay citas con este estado.
          </div>
        ) : (
          citasFiltradas.map((v, i) => {
            const config = VARIANTE_VEREDICTO[v.veredicto] || VARIANTE_VEREDICTO['NO_VERIFICABLE']
            const abierto = expandido === v.cita_id

            return (
              <div
                key={v.cita_id}
                style={{
                  background: 'var(--bg-surface-2)',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid transparent',
                  animation: `fadeIn 0.3s ease ${i * 0.03}s forwards`,
                  opacity: 0,
                  overflow: 'hidden',
                }}
              >
                {/* Fila principal */}
                <div
                  onClick={() => setExpandido(abierto ? null : v.cita_id)}
                  style={{
                    padding: '0.875rem',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '0.75rem',
                  }}
                >
                  {/* Veredicto badge */}
                  <div style={{ flexShrink: 0, marginTop: '2px' }}>
                    <Badge
                      texto={v.veredicto}
                      variante={config.variante}
                      icono={config.icono}
                    />
                  </div>

                  {/* Contenido */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontFamily: 'var(--font-mono)',
                      fontSize: '0.85rem',
                      color: 'var(--accent)',
                      fontWeight: 600,
                      marginBottom: '0.25rem',
                    }}>
                      {v.texto_cita}
                    </div>
                    <div style={{
                      fontSize: '0.78rem',
                      color: 'var(--text-secondary)',
                      lineHeight: 1.5,
                    }}>
                      {v.justificacion}
                    </div>
                    {RAGAS_BADGES.some(b => v[b.key] != null) && (
                      <div style={{
                        display: 'flex',
                        flexWrap: 'wrap',
                        gap: '0.3rem',
                        marginTop: '0.5rem',
                      }}>
                        {RAGAS_BADGES.map(b => {
                          const val = v[b.key]
                          if (val == null) return null
                          return (
                            <span
                              key={b.key}
                              title={b.key}
                              style={{
                                padding: '0.15rem 0.45rem',
                                borderRadius: '999px',
                                background: bgRagas(val),
                                color: colorRagas(val),
                                fontSize: '0.68rem',
                                fontWeight: 600,
                                fontFamily: 'var(--font-mono)',
                                border: `1px solid ${colorRagas(val)}33`,
                              }}
                            >
                              {b.sigla}: {val.toFixed(2)}
                            </span>
                          )
                        })}
                      </div>
                    )}
                  </div>

                  {/* Página + expandir */}
                  <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'flex-end',
                    gap: '0.3rem',
                    flexShrink: 0,
                  }}>
                    <span style={{
                      fontSize: '0.7rem',
                      color: 'var(--text-muted)',
                      fontFamily: 'var(--font-mono)',
                    }}>
                      p. {v.pagina}
                    </span>
                    <span style={{
                      fontSize: '0.7rem',
                      color: 'var(--text-muted)',
                    }}>
                      {abierto ? '▲' : '▼'}
                    </span>
                  </div>
                </div>

                {/* Detalle expandido */}
                {abierto && (
                  <div style={{
                    padding: '0 0.875rem 0.875rem',
                    borderTop: '1px solid var(--border)',
                    paddingTop: '0.75rem',
                    animation: 'fadeIn 0.2s ease forwards',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '0.75rem',
                  }}>

                    {/* Afirmación del tesista */}
                    <div>
                      <div style={{
                        fontSize: '0.7rem',
                        color: 'var(--text-muted)',
                        textTransform: 'uppercase',
                        letterSpacing: '0.05em',
                        marginBottom: '0.35rem',
                      }}>
                        Afirmación del tesista
                      </div>
                      <div style={{
                        fontSize: '0.83rem',
                        color: 'var(--text-primary)',
                        fontStyle: 'italic',
                        lineHeight: 1.6,
                        borderLeft: '2px solid var(--accent)',
                        paddingLeft: '0.75rem',
                      }}>
                        {v.fragmento_oracion
                          ? `"...${v.fragmento_oracion}..."`
                          : `"${v.texto_cita}"`
                        }
                        <span style={{
                          marginLeft: '0.5rem',
                          fontSize: '0.72rem',
                          color: 'var(--text-muted)',
                          fontFamily: 'var(--font-mono)',
                          fontStyle: 'normal',
                        }}>
                          p. {v.pagina}
                        </span>
                      </div>
                    </div>

                    {/* Paper citado */}
                    {(v.titulo_referencia || v.autores_referencia?.length > 0) && (
                      <div style={{
                        padding: '0.75rem',
                        background: 'var(--bg-surface)',
                        borderRadius: 'var(--radius-md)',
                        border: '1px solid var(--border)',
                      }}>
                        <div style={{
                          fontSize: '0.7rem',
                          color: 'var(--text-muted)',
                          textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                          marginBottom: '0.4rem',
                        }}>
                          Paper citado
                        </div>
                        {v.titulo_referencia && (
                          <div style={{
                            fontSize: '0.85rem',
                            fontWeight: 600,
                            color: 'var(--text-primary)',
                            marginBottom: '0.25rem',
                            lineHeight: 1.4,
                          }}>
                            {v.titulo_referencia}
                          </div>
                        )}
                        <div style={{
                          fontSize: '0.78rem',
                          color: 'var(--text-secondary)',
                          display: 'flex',
                          gap: '0.5rem',
                          flexWrap: 'wrap',
                          alignItems: 'center',
                        }}>
                          {v.autores_referencia?.length > 0 && (
                            <span>{v.autores_referencia.join(', ')}</span>
                          )}
                          {v.anio_referencia && (
                            <span style={{
                              fontFamily: 'var(--font-mono)',
                              color: 'var(--accent)',
                            }}>
                              ({v.anio_referencia})
                            </span>
                          )}
                          {v.doi_referencia && (
                            <span style={{
                              fontSize: '0.7rem',
                              color: 'var(--text-muted)',
                              fontFamily: 'var(--font-mono)',
                            }}>
                              DOI: {v.doi_referencia}
                            </span>
                          )}
                          {v.pagina_paper != null && (
                            <span style={{
                              fontSize: '0.7rem',
                              color: 'var(--text-muted)',
                              fontFamily: 'var(--font-mono)',
                            }}>
                              p. {v.pagina_paper}
                            </span>
                          )}
                        </div>
                      </div>
                    )}

                    {/* Fragmento del paper */}
                    {v.fragmento_evidencia && (
                      <div>
                        <div style={{
                          fontSize: '0.7rem',
                          color: 'var(--text-muted)',
                          textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                          marginBottom: '0.35rem',
                        }}>
                          Contenido original del paper
                        </div>
                        <div style={{
                          fontSize: '0.8rem',
                          color: 'var(--text-secondary)',
                          fontStyle: 'italic',
                          lineHeight: 1.6,
                          background: 'var(--bg-surface)',
                          padding: '0.75rem',
                          borderRadius: '0 var(--radius-sm) var(--radius-sm) 0',
                          borderLeft: '2px solid var(--border)',
                        }}>
                          "{v.fragmento_evidencia}"
                        </div>
                        {v.similitud > 0 && (
                          <div style={{
                            marginTop: '0.3rem',
                            fontSize: '0.72rem',
                            color: 'var(--text-muted)',
                            fontFamily: 'var(--font-mono)',
                          }}>
                            Similitud semántica: {(v.similitud * 100).toFixed(1)}%
                          </div>
                        )}
                      </div>
                    )}

                    {/* Motivo del veredicto */}
                    <div style={{
                      padding: '0.75rem',
                      background: config.variante === 'success'
                        ? 'var(--success-subtle)'
                        : config.variante === 'warning'
                        ? 'var(--warning-subtle)'
                        : config.variante === 'error'
                        ? 'var(--error-subtle)'
                        : 'var(--bg-surface-2)',
                      borderRadius: 'var(--radius-md)',
                      fontSize: '0.8rem',
                      color: 'var(--text-secondary)',
                      lineHeight: 1.5,
                    }}>
                      <span style={{ fontWeight: 600 }}>Motivo: </span>
                      {v.justificacion}
                    </div>
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

function Chip({ label, valor, variante }) {
  const colores = {
    success: 'var(--success)',
    warning: 'var(--warning)',
    error:   'var(--error)',
    neutral: 'var(--text-muted)',
  }
  return (
    <div style={{
      padding: '0.6rem 0.5rem',
      background: 'var(--bg-surface-2)',
      borderRadius: 'var(--radius-md)',
      textAlign: 'center',
    }}>
      <div style={{
        fontSize: '0.65rem',
        color: 'var(--text-muted)',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        marginBottom: '0.1rem',
      }}>
        {label}
      </div>
      <div style={{
        fontSize: '1.1rem',
        fontWeight: 700,
        color: colores[variante] || 'var(--text-primary)',
        fontFamily: 'var(--font-mono)',
      }}>
        {valor}
      </div>
    </div>
  )
}
import Badge from '../ui/Badge'

export default function ListaReferencias({ referencias, total, advertencia }) {
  return (
    <div>
      {/* Header con total */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '1rem',
      }}>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          {total} referencias detectadas
        </span>
        {advertencia && (
          <Badge texto="Datos incompletos" variante="warning" icono="⚠️" />
        )}
      </div>

      {/* Advertencia general */}
      {advertencia && (
        <div style={{
          padding: '0.75rem 1rem',
          background: 'var(--warning-subtle)',
          border: '1px solid rgba(245, 158, 11, 0.2)',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.82rem',
          color: 'var(--warning)',
          marginBottom: '1rem',
          display: 'flex',
          gap: '0.5rem',
        }}>
          <span>⚠️</span>
          <span>{advertencia}</span>
        </div>
      )}

      {/* Lista */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        overflowY: 'auto',
        paddingRight: '0.25rem',
      }}>
        {referencias.map((ref, i) => (
          <div
            key={ref.referencia_id}
            style={{
              padding: '0.875rem',
              background: 'var(--bg-surface-2)',
              borderRadius: 'var(--radius-md)',
              border: ref.datos_incompletos
                ? '1px solid rgba(245, 158, 11, 0.2)'
                : '1px solid transparent',
              animation: `fadeIn 0.3s ease ${i * 0.03}s forwards`,
              opacity: 0,
            }}
          >
            {/* Título */}
            <div style={{
              fontSize: '0.85rem',
              fontWeight: 600,
              color: 'var(--text-primary)',
              marginBottom: '0.3rem',
              lineHeight: 1.4,
            }}>
              {ref.titulo}
            </div>

            {/* Autores y año */}
            <div style={{
              fontSize: '0.78rem',
              color: 'var(--text-secondary)',
              marginBottom: '0.5rem',
            }}>
              {ref.autores.join(', ')}
              {ref.anio && (
                <span style={{
                  marginLeft: '0.5rem',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--accent)',
                }}>
                  ({ref.anio})
                </span>
              )}
            </div>

            {/* Footer */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              flexWrap: 'wrap',
            }}>
              {ref.fuente && (
                <span style={{
                  fontSize: '0.72rem',
                  color: 'var(--text-muted)',
                  fontStyle: 'italic',
                }}>
                  {ref.fuente}
                </span>
              )}
              {ref.doi && (
                <span style={{
                  fontSize: '0.7rem',
                  color: 'var(--text-muted)',
                  fontFamily: 'var(--font-mono)',
                }}>
                  DOI: {ref.doi}
                </span>
              )}
              {ref.nivel_confianza && (
                <Badge
                  texto={ref.nivel_confianza === 'texto_completo'
                    ? 'Texto completo'
                    : ref.nivel_confianza === 'abstract'
                    ? 'Abstract'
                    : 'No verificado'}
                  variante={ref.nivel_confianza === 'texto_completo'
                    ? 'success'
                    : ref.nivel_confianza === 'abstract'
                    ? 'accent'
                    : 'neutral'}
                  icono={ref.nivel_confianza === 'texto_completo' ? '✓' : null}
                />
              )}
            </div>

            {/* Campos faltantes */}
            {ref.datos_incompletos && (
              (() => {
                const faltantes = ref.campos_faltantes?.length > 0
                  ? ref.campos_faltantes
                  : [
                      !ref.autores?.length && 'autores',
                      !ref.anio && 'año',
                      !ref.titulo && 'título',
                      !ref.fuente && 'fuente/revista',
                      !ref.doi && 'DOI',
                    ].filter(Boolean)

                return faltantes.length > 0 ? (
                  <div style={{
                    marginTop: '0.5rem',
                    padding: '0.5rem 0.75rem',
                    background: 'var(--warning-subtle)',
                    border: '1px solid rgba(245,158,11,0.2)',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '0.75rem',
                    color: 'var(--warning)',
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '0.4rem',
                  }}>
                    <span>⚠️</span>
                    <div>
                      <span style={{ fontWeight: 600 }}>Faltan: </span>
                      {faltantes.join(', ')}
                    </div>
                  </div>
                ) : (
                  <div style={{
                    marginTop: '0.5rem',
                    padding: '0.5rem 0.75rem',
                    background: 'var(--warning-subtle)',
                    border: '1px solid rgba(245,158,11,0.2)',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '0.75rem',
                    color: 'var(--warning)',
                    display: 'flex',
                    gap: '0.4rem',
                  }}>
                    <span>⚠️</span>
                    <span>Referencia marcada como incompleta por el analizador.</span>
                  </div>
                )
              })()
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
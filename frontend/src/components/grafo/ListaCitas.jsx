import Badge from '../ui/Badge'

export default function ListaCitas({ citas, total, parenteticas, narrativas, advertencia }) {
  return (
    <div>
      {/* Métricas */}
      <div style={{
        display: 'flex',
        gap: '0.75rem',
        marginBottom: '1rem',
        flexWrap: 'wrap',
      }}>
        <Chip label="Total" valor={total} />
        <Chip label="Parentéticas" valor={parenteticas} />
        <Chip label="Narrativas" valor={narrativas} />
      </div>

      {/* Advertencia */}
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
      {citas.length === 0 ? (
        <div style={{
          textAlign: 'center',
          padding: '2rem',
          color: 'var(--text-muted)',
          fontSize: '0.85rem',
        }}>
          No se detectaron citas APA en este documento.
        </div>
      ) : (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.5rem',
          maxHeight: '420px',
          overflowY: 'auto',
          paddingRight: '0.25rem',
        }}>
          {citas.map((cita, i) => (
            <div
              key={cita.cita_id}
              style={{
                padding: '0.875rem',
                background: 'var(--bg-surface-2)',
                borderRadius: 'var(--radius-md)',
                animation: `fadeIn 0.3s ease ${i * 0.03}s forwards`,
                opacity: 0,
              }}
            >
              {/* Texto de la cita */}
              <div style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '0.85rem',
                color: 'var(--accent)',
                fontWeight: 600,
                marginBottom: '0.4rem',
              }}>
                {cita.texto_cita}
              </div>

              {/* Fragmento de contexto */}
              {cita.fragmento_oracion && (
                <div style={{
                  fontSize: '0.78rem',
                  color: 'var(--text-secondary)',
                  fontStyle: 'italic',
                  marginBottom: '0.5rem',
                  lineHeight: 1.5,
                  borderLeft: '2px solid var(--border)',
                  paddingLeft: '0.75rem',
                }}>
                  "...{cita.fragmento_oracion}..."
                </div>
              )}

              {/* Footer */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
              }}>
                <Badge
                  texto={cita.tipo === 'parentetica' ? 'Parentética' : 'Narrativa'}
                  variante={cita.tipo === 'parentetica' ? 'accent' : 'neutral'}
                />
                <span style={{
                  fontSize: '0.72rem',
                  color: 'var(--text-muted)',
                  fontFamily: 'var(--font-mono)',
                }}>
                  p. {cita.pagina}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Chip({ label, valor }) {
  return (
    <div style={{
      padding: '0.4rem 0.875rem',
      background: 'var(--bg-surface-2)',
      borderRadius: 'var(--radius-md)',
      textAlign: 'center',
    }}>
      <div style={{
        fontSize: '0.68rem',
        color: 'var(--text-muted)',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        marginBottom: '0.1rem',
      }}>
        {label}
      </div>
      <div style={{
        fontSize: '1rem',
        fontWeight: 700,
        color: 'var(--accent)',
        fontFamily: 'var(--font-mono)',
      }}>
        {valor}
      </div>
    </div>
  )
}
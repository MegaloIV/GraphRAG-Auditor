export default function AlertasAlucinaciones({ datos }) {
  const { total_no_verificables, alertas, advertencia } = datos

  if (total_no_verificables === 0) {
    return (
      <div style={{
        padding: '1.5rem',
        background: 'var(--success-subtle)',
        border: '1px solid rgba(16,185,129,0.2)',
        borderRadius: 'var(--radius-md)',
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
        animation: 'fadeIn 0.3s ease forwards',
      }}>
        <span style={{ fontSize: '1.5rem' }}>✅</span>
        <div>
          <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--success)' }}>
            Sin alertas de verificación
          </div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
            Todas las citas pudieron verificarse contra el grafo de conocimiento.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ animation: 'fadeIn 0.3s ease forwards' }}>

      {/* Advertencia principal */}
      {advertencia && (
        <div style={{
          padding: '1rem',
          background: 'var(--error-subtle)',
          border: '1px solid rgba(239,68,68,0.2)',
          borderRadius: 'var(--radius-md)',
          marginBottom: '1.25rem',
          display: 'flex',
          gap: '0.75rem',
          alignItems: 'flex-start',
        }}>
          <span style={{ fontSize: '1.25rem', flexShrink: 0 }}>⚠️</span>
          <div>
            <div style={{
              fontSize: '0.88rem',
              fontWeight: 600,
              color: 'var(--error)',
              marginBottom: '0.25rem',
            }}>
              Requieren revisión manual
            </div>
            <div style={{
              fontSize: '0.8rem',
              color: 'var(--text-secondary)',
              lineHeight: 1.5,
            }}>
              {advertencia}
            </div>
          </div>
        </div>
      )}

      {/* Lista de alertas */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        maxHeight: '420px',
        overflowY: 'auto',
        paddingRight: '0.25rem',
      }}>
        {alertas.map((alerta, i) => (
          <div
            key={alerta.cita_id}
            style={{
              padding: '0.875rem',
              background: 'var(--bg-surface-2)',
              borderRadius: 'var(--radius-md)',
              borderLeft: '3px solid var(--error)',
              animation: `fadeIn 0.3s ease ${i * 0.04}s forwards`,
              opacity: 0,
            }}
          >
            {/* Cita */}
            <div style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '0.85rem',
              color: 'var(--accent)',
              fontWeight: 600,
              marginBottom: '0.35rem',
            }}>
              {alerta.texto_cita}
            </div>

            {/* Razón */}
            <div style={{
              fontSize: '0.78rem',
              color: 'var(--text-secondary)',
              lineHeight: 1.5,
              marginBottom: '0.4rem',
            }}>
              {alerta.razon_no_verificable}
            </div>

            {/* Página */}
            <div style={{
              fontSize: '0.7rem',
              color: 'var(--text-muted)',
              fontFamily: 'var(--font-mono)',
            }}>
              p. {alerta.pagina}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
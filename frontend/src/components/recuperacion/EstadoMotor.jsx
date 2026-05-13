import Badge from '../ui/Badge'

export default function EstadoMotor({ datos, onAuditar, auditando }) {
  if (!datos) return null

  const { listo, total_chunks, total_citas_vinculadas, total_referencias, mensaje } = datos

  return (
    <div style={{ animation: 'fadeIn 0.3s ease forwards' }}>

      {/* Estado principal */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
        padding: '1rem',
        background: listo ? 'var(--success-subtle)' : 'var(--warning-subtle)',
        border: `1px solid ${listo ? 'rgba(16,185,129,0.2)' : 'rgba(245,158,11,0.2)'}`,
        borderRadius: 'var(--radius-md)',
        marginBottom: '1.25rem',
      }}>
        <span style={{ fontSize: '1.5rem' }}>{listo ? '🔍' : '⚠️'}</span>
        <div style={{ flex: 1 }}>
          <div style={{
            fontSize: '0.9rem',
            fontWeight: 600,
            color: listo ? 'var(--success)' : 'var(--warning)',
          }}>
            {listo ? 'Motor listo para auditar' : 'Motor no disponible'}
          </div>
          <div style={{
            fontSize: '0.78rem',
            color: 'var(--text-secondary)',
            marginTop: '2px',
          }}>
            {mensaje}
          </div>
        </div>
        <Badge
          texto={listo ? 'Listo' : 'No listo'}
          variante={listo ? 'success' : 'warning'}
        />
      </div>

      {/* Métricas */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: '0.75rem',
        marginBottom: '1.5rem',
      }}>
        <Metrica icono="📄" label="Fragmentos indexados" valor={total_chunks} />
        <Metrica icono="🔗" label="Citas vinculadas" valor={total_citas_vinculadas} />
        <Metrica icono="📚" label="Referencias" valor={total_referencias} />
      </div>

      {/* Botón auditar */}
      <button
        onClick={onAuditar}
        disabled={!listo || auditando}
        style={{
          width: '100%',
          padding: '0.875rem',
          background: !listo || auditando ? 'var(--bg-surface-2)' : 'var(--accent)',
          color: !listo || auditando ? 'var(--text-muted)' : 'white',
          border: 'none',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.95rem',
          fontWeight: 600,
          cursor: !listo || auditando ? 'not-allowed' : 'pointer',
          fontFamily: 'var(--font-sans)',
          transition: 'background 0.2s ease',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '0.5rem',
        }}
      >
        {auditando ? (
          <>
            <div style={{
              width: '16px',
              height: '16px',
              border: '2px solid var(--border)',
              borderTop: '2px solid var(--text-muted)',
              borderRadius: '50%',
              animation: 'spin 1s linear infinite',
            }} />
            Auditando citas...
          </>
        ) : (
          '🔍 Iniciar Auditoría Semántica'
        )}
      </button>

      {!listo && (
        <div style={{
          marginTop: '0.75rem',
          fontSize: '0.78rem',
          color: 'var(--text-muted)',
          textAlign: 'center',
        }}>
          El motor estará disponible una vez que CrossRef verifique las referencias.
        </div>
      )}
    </div>
  )
}

function Metrica({ icono, label, valor }) {
  return (
    <div style={{
      padding: '0.875rem',
      background: 'var(--bg-surface-2)',
      borderRadius: 'var(--radius-md)',
      display: 'flex',
      alignItems: 'center',
      gap: '0.6rem',
    }}>
      <div style={{
        width: '30px',
        height: '30px',
        background: 'var(--accent-subtle)',
        borderRadius: 'var(--radius-sm)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '0.85rem',
        flexShrink: 0,
      }}>
        {icono}
      </div>
      <div>
        <div style={{
          fontSize: '0.65rem',
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
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
    </div>
  )
}
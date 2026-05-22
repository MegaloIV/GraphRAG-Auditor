import Badge from '../ui/Badge'

export default function AlertasInconsistencias({ datos }) {
  const { citas_sin_referencia, referencias_sin_citar, total_inconsistencias, mensaje } = datos

  if (total_inconsistencias === 0) {
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
            Sin inconsistencias
          </div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
            {mensaje}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ animation: 'fadeIn 0.3s ease forwards' }}>

      {/* Resumen */}
      <div style={{
        display: 'flex',
        gap: '0.75rem',
        marginBottom: '1.25rem',
        flexWrap: 'wrap',
      }}>
        <ChipAlerta
          label="Citas sin referencia"
          valor={citas_sin_referencia.length}
          activo={citas_sin_referencia.length > 0}
        />
        <ChipAlerta
          label="Referencias sin citar"
          valor={referencias_sin_citar.length}
          activo={referencias_sin_citar.length > 0}
        />
      </div>

      {/* Citas sin referencia */}
      {citas_sin_referencia.length > 0 && (
        <Seccion
          titulo="Citas sin referencia bibliográfica"
          icono="💬"
          items={citas_sin_referencia}
          variante="error"
        />
      )}

      {/* Referencias sin citar */}
      {referencias_sin_citar.length > 0 && (
        <Seccion
          titulo="Referencias listadas sin citar en el texto"
          icono="📚"
          items={referencias_sin_citar}
          variante="warning"
        />
      )}
    </div>
  )
}

function Seccion({ titulo, icono, items, variante }) {
  return (
    <div style={{ marginBottom: '1.25rem' }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        marginBottom: '0.75rem',
      }}>
        <span>{icono}</span>
        <span style={{
          fontSize: '0.85rem',
          fontWeight: 600,
          color: 'var(--text-primary)',
        }}>
          {titulo}
        </span>
        <Badge texto={items.length} variante={variante} />
      </div>

      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.4rem',
        maxHeight: '280px',
        overflowY: 'auto',
      }}>
        {items.map((item, i) => (
          <div
            key={i}
            style={{
              padding: '0.75rem',
              background: 'var(--bg-surface-2)',
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '0.75rem',
              animation: `fadeIn 0.3s ease ${i * 0.04}s forwards`,
              opacity: 0,
            }}
          >
            <div style={{
              width: '6px',
              height: '6px',
              borderRadius: '50%',
              background: variante === 'error' ? 'var(--error)' : 'var(--warning)',
              flexShrink: 0,
              marginTop: '6px',
            }} />
            <div style={{ flex: 1 }}>
              <div style={{
                fontSize: '0.83rem',
                color: 'var(--text-primary)',
                fontWeight: 500,
                marginBottom: '0.2rem',
                fontFamily: item.tipo === 'cita_sin_referencia' ? 'var(--font-mono)' : 'var(--font-sans)',
              }}>
                {item.elemento}
              </div>
              <div style={{
                fontSize: '0.72rem',
                color: 'var(--text-muted)',
              }}>
                {item.ubicacion}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function ChipAlerta({ label, valor, activo }) {
  return (
    <div style={{
      padding: '0.5rem 1rem',
      background: activo ? 'var(--error-subtle)' : 'var(--bg-surface-2)',
      border: `1px solid ${activo ? 'rgba(239,68,68,0.2)' : 'var(--border)'}`,
      borderRadius: 'var(--radius-md)',
      textAlign: 'center',
    }}>
      <div style={{
        fontSize: '0.68rem',
        color: 'var(--text-muted)',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
      }}>
        {label}
      </div>
      <div style={{
        fontSize: '1.1rem',
        fontWeight: 700,
        color: activo ? 'var(--error)' : 'var(--text-muted)',
        fontFamily: 'var(--font-mono)',
      }}>
        {valor}
      </div>
    </div>
  )
}
export default function BarraProgreso({ porcentaje, mensaje, estado }) {
  const colorBarra = estado === 'error'
    ? 'var(--error)'
    : estado === 'completado'
    ? 'var(--success)'
    : 'var(--accent)'

  return (
    <div style={{ width: '100%' }}>
      {/* Mensaje y porcentaje */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '0.6rem',
      }}>
        <span style={{
          fontSize: '0.85rem',
          color: 'var(--text-secondary)',
        }}>
          {mensaje}
        </span>
        <span style={{
          fontSize: '0.85rem',
          fontWeight: 600,
          color: colorBarra,
          fontFamily: 'var(--font-mono)',
        }}>
          {porcentaje}%
        </span>
      </div>

      {/* Barra */}
      <div style={{
        width: '100%',
        height: '6px',
        background: 'var(--bg-surface-2)',
        borderRadius: '999px',
        overflow: 'hidden',
      }}>
        <div style={{
          height: '100%',
          width: `${porcentaje}%`,
          background: colorBarra,
          borderRadius: '999px',
          transition: 'width 0.4s ease',
          boxShadow: `0 0 8px ${colorBarra}66`,
        }} />
      </div>

      {/* Estado completado */}
      {estado === 'completado' && (
        <div style={{
          marginTop: '0.75rem',
          fontSize: '0.8rem',
          color: 'var(--success)',
          display: 'flex',
          alignItems: 'center',
          gap: '0.4rem',
        }}>
          ✓ Procesamiento completado
        </div>
      )}

      {/* Estado error */}
      {estado === 'error' && (
        <div style={{
          marginTop: '0.75rem',
          fontSize: '0.8rem',
          color: 'var(--error)',
          display: 'flex',
          alignItems: 'center',
          gap: '0.4rem',
        }}>
          ✕ Error en el procesamiento
        </div>
      )}
    </div>
  )
}
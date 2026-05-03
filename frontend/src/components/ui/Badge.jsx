const VARIANTES = {
  success: {
    background: 'var(--success-subtle)',
    color: 'var(--success)',
    border: 'rgba(16, 185, 129, 0.2)',
  },
  warning: {
    background: 'var(--warning-subtle)',
    color: 'var(--warning)',
    border: 'rgba(245, 158, 11, 0.2)',
  },
  error: {
    background: 'var(--error-subtle)',
    color: 'var(--error)',
    border: 'rgba(239, 68, 68, 0.2)',
  },
  accent: {
    background: 'var(--accent-subtle)',
    color: 'var(--accent)',
    border: 'rgba(59, 130, 246, 0.2)',
  },
  neutral: {
    background: 'var(--bg-surface-2)',
    color: 'var(--text-secondary)',
    border: 'var(--border)',
  },
}

export default function Badge({ texto, variante = 'neutral', icono }) {
  const estilos = VARIANTES[variante] || VARIANTES.neutral

  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '0.3rem',
      padding: '0.2rem 0.6rem',
      borderRadius: '999px',
      fontSize: '0.72rem',
      fontWeight: 600,
      letterSpacing: '0.02em',
      background: estilos.background,
      color: estilos.color,
      border: `1px solid ${estilos.border}`,
      whiteSpace: 'nowrap',
    }}>
      {icono && <span>{icono}</span>}
      {texto}
    </span>
  )
}
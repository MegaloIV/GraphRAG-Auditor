export default function Card({
  children,
  titulo,
  subtitulo,
  icono,
  style = {},
  padding = '1.5rem',
}) {
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius-lg)',
      padding,
      boxShadow: 'var(--shadow-sm)',
      animation: 'fadeIn 0.3s ease forwards',
      ...style,
    }}>
      {(titulo || icono) && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          marginBottom: subtitulo ? '0.25rem' : '1.25rem',
        }}>
          {icono && (
            <div style={{
              width: '36px',
              height: '36px',
              background: 'var(--accent-subtle)',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--accent)',
              fontSize: '1rem',
              flexShrink: 0,
            }}>
              {icono}
            </div>
          )}
          <div>
            {titulo && (
              <h3 style={{
                fontSize: '0.95rem',
                fontWeight: 600,
                color: 'var(--text-primary)',
                lineHeight: 1.3,
              }}>
                {titulo}
              </h3>
            )}
            {subtitulo && (
              <p style={{
                fontSize: '0.75rem',
                color: 'var(--text-muted)',
                marginTop: '2px',
              }}>
                {subtitulo}
              </p>
            )}
          </div>
        </div>
      )}
      {children}
    </div>
  )
}
import Badge from '../ui/Badge'

const ICONOS_SECCION = {
  introduccion: '📖',
  resumen: '📝',
  metodologia: '🔬',
  resultados: '📊',
  discusion: '💬',
  conclusion: '✅',
  referencias: '📚',
  cuerpo: '📄',
  desconocido: '❓',
}

const LABELS_SECCION = {
  introduccion: 'Introducción',
  resumen: 'Resumen / Abstract',
  metodologia: 'Metodología',
  resultados: 'Resultados',
  discusion: 'Discusión',
  conclusion: 'Conclusión',
  referencias: 'Referencias',
  cuerpo: 'Cuerpo',
  desconocido: 'Sección desconocida',
}

export default function EstructuraDocumento({ datos, onConfirmar, cargando }) {
  return (
    <div style={{ animation: 'fadeIn 0.3s ease forwards' }}>

      {/* Info del documento */}
      <div style={{
        display: 'flex',
        gap: '1rem',
        marginBottom: '1.5rem',
        flexWrap: 'wrap',
      }}>
        <Metrica label="Páginas" valor={datos.total_paginas} />
        <Metrica label="Secciones" valor={datos.secciones.length} />
        <Metrica
          label="Referencias"
          valor={datos.tiene_seccion_referencias ? 'Detectada' : 'No detectada'}
          variante={datos.tiene_seccion_referencias ? 'success' : 'error'}
        />
      </div>

      {/* Advertencia si no hay referencias */}
      {datos.advertencia && (
        <div style={{
          padding: '0.75rem 1rem',
          background: 'var(--warning-subtle)',
          border: '1px solid rgba(245, 158, 11, 0.2)',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.82rem',
          color: 'var(--warning)',
          marginBottom: '1.25rem',
          display: 'flex',
          gap: '0.5rem',
          alignItems: 'flex-start',
        }}>
          <span>⚠️</span>
          <span>{datos.advertencia}</span>
        </div>
      )}

      {/* Lista de secciones */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
        marginBottom: '1.5rem',
      }}>
        {datos.secciones.map((seccion, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0.6rem 0.875rem',
              background: 'var(--bg-surface-2)',
              borderRadius: 'var(--radius-md)',
              border: seccion.tipo === 'referencias'
                ? '1px solid rgba(16, 185, 129, 0.2)'
                : '1px solid transparent',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
              <span>{ICONOS_SECCION[seccion.tipo] || '📄'}</span>
              <div>
                <div style={{
                  fontSize: '0.85rem',
                  fontWeight: 500,
                  color: 'var(--text-primary)',
                }}>
                  {LABELS_SECCION[seccion.tipo] || seccion.titulo_detectado}
                </div>
                <div style={{
                  fontSize: '0.72rem',
                  color: 'var(--text-muted)',
                  fontFamily: 'var(--font-mono)',
                }}>
                  p. {seccion.pagina_inicio} — {seccion.pagina_fin}
                </div>
              </div>
            </div>
            {seccion.tipo === 'referencias' && (
              <Badge texto="Referencias" variante="success" icono="✓" />
            )}
          </div>
        ))}
      </div>

      {/* Botón confirmar */}
      <button
        onClick={onConfirmar}
        disabled={cargando}
        style={{
          width: '100%',
          padding: '0.875rem',
          background: cargando ? 'var(--bg-surface-2)' : 'var(--accent)',
          color: cargando ? 'var(--text-muted)' : 'white',
          border: 'none',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.95rem',
          fontWeight: 600,
          cursor: cargando ? 'not-allowed' : 'pointer',
          fontFamily: 'var(--font-sans)',
          transition: 'background 0.2s ease',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '0.5rem',
        }}
      >
        {cargando ? (
          <>
            <div style={{
              width: '16px',
              height: '16px',
              border: '2px solid var(--border)',
              borderTop: '2px solid var(--text-muted)',
              borderRadius: '50%',
              animation: 'spin 1s linear infinite',
            }} />
            Iniciando auditoría...
          </>
        ) : (
          '🚀 Iniciar Auditoría'
        )}
      </button>
    </div>
  )
}

function Metrica({ label, valor, variante }) {
  return (
    <div style={{
      padding: '0.6rem 1rem',
      background: 'var(--bg-surface-2)',
      borderRadius: 'var(--radius-md)',
      textAlign: 'center',
      minWidth: '100px',
    }}>
      <div style={{
        fontSize: '0.7rem',
        color: 'var(--text-muted)',
        marginBottom: '0.2rem',
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
      }}>
        {label}
      </div>
      <div style={{
        fontSize: '1rem',
        fontWeight: 700,
        color: variante === 'success'
          ? 'var(--success)'
          : variante === 'error'
          ? 'var(--error)'
          : 'var(--accent)',
        fontFamily: 'var(--font-mono)',
      }}>
        {valor}
      </div>
    </div>
  )
}
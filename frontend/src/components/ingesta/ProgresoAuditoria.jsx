import BarraProgreso from '../ui/BarraProgreso'

const ETAPAS = [
  { porcentaje: 10,  label: 'Extrayendo texto del PDF' },
  { porcentaje: 25,  label: 'Analizando estructura' },
  { porcentaje: 40,  label: 'Extrayendo referencias' },
  { porcentaje: 60,  label: 'Detectando citas' },
  { porcentaje: 75,  label: 'Construyendo grafo' },
  { porcentaje: 80,  label: 'Verificando en CrossRef' },
  { porcentaje: 100, label: 'Completado' },
]

export default function ProgresoAuditoria({ progreso }) {
  const { porcentaje, mensaje_progreso, estado, citas_encontradas, error } = progreso

  return (
    <div style={{ animation: 'fadeIn 0.3s ease forwards' }}>

      {/* Barra principal */}
      <div style={{ marginBottom: '2rem' }}>
        <BarraProgreso
          porcentaje={porcentaje}
          mensaje={mensaje_progreso}
          estado={estado}
        />
      </div>

      {/* Etapas */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.4rem',
        marginBottom: '1.5rem',
      }}>
        {ETAPAS.map((etapa, i) => {
          const completada = porcentaje >= etapa.porcentaje
          const activa = porcentaje < etapa.porcentaje &&
            (i === 0 || porcentaje >= ETAPAS[i - 1].porcentaje)

          return (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.75rem',
                padding: '0.5rem 0.75rem',
                borderRadius: 'var(--radius-md)',
                background: activa ? 'var(--accent-subtle)' : 'transparent',
                transition: 'background 0.3s ease',
              }}
            >
              {/* Indicador */}
              <div style={{
                width: '20px',
                height: '20px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
                fontSize: '0.7rem',
                background: completada
                  ? 'var(--success)'
                  : activa
                  ? 'var(--accent)'
                  : 'var(--bg-surface-2)',
                border: activa
                  ? '2px solid var(--accent)'
                  : 'none',
              }}>
                {completada ? '✓' : activa ? (
                  <div style={{
                    width: '8px',
                    height: '8px',
                    border: '2px solid white',
                    borderTop: '2px solid transparent',
                    borderRadius: '50%',
                    animation: 'spin 1s linear infinite',
                  }} />
                ) : null}
              </div>

              {/* Label */}
              <span style={{
                fontSize: '0.82rem',
                color: completada
                  ? 'var(--success)'
                  : activa
                  ? 'var(--accent)'
                  : 'var(--text-muted)',
                fontWeight: activa ? 600 : 400,
              }}>
                {etapa.label}
              </span>

              {/* Porcentaje */}
              <span style={{
                marginLeft: 'auto',
                fontSize: '0.72rem',
                color: 'var(--text-muted)',
                fontFamily: 'var(--font-mono)',
              }}>
                {etapa.porcentaje}%
              </span>
            </div>
          )
        })}
      </div>

      {/* Resultado final */}
      {estado === 'completado' && (
        <div style={{
          padding: '1rem',
          background: 'var(--success-subtle)',
          border: '1px solid rgba(16, 185, 129, 0.2)',
          borderRadius: 'var(--radius-md)',
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          animation: 'fadeIn 0.3s ease forwards',
        }}>
          <span style={{ fontSize: '1.5rem' }}>✅</span>
          <div>
            <div style={{
              fontSize: '0.9rem',
              fontWeight: 600,
              color: 'var(--success)',
            }}>
              Auditoría completada
            </div>
            {citas_encontradas !== null && (
              <div style={{
                fontSize: '0.8rem',
                color: 'var(--text-secondary)',
                marginTop: '2px',
              }}>
                {citas_encontradas} citas procesadas
              </div>
            )}
          </div>
        </div>
      )}

      {/* Error */}
      {estado === 'error' && (
        <div style={{
          padding: '1rem',
          background: 'var(--error-subtle)',
          border: '1px solid rgba(239, 68, 68, 0.2)',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.85rem',
          color: 'var(--error)',
        }}>
          ✕ {error || 'Ocurrió un error durante el procesamiento.'}
        </div>
      )}
    </div>
  )
}
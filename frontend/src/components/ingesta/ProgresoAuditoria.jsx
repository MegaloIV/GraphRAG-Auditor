import BarraProgreso from '../ui/BarraProgreso'

const ETAPAS_EXTRACCION = [
  { porcentaje: 10,  label: 'Extrayendo texto del PDF' },
  { porcentaje: 25,  label: 'Analizando estructura' },
  { porcentaje: 40,  label: 'Extrayendo referencias' },
  { porcentaje: 55,  label: 'Detectando citas' },
  { porcentaje: 70,  label: 'Construyendo grafo' },
  { porcentaje: 90,  label: 'Vinculando citas con referencias' },
  { porcentaje: 100, label: 'Extracción completada' },
]

const ETAPAS_VERIFICACION = [
  { porcentaje: 10,  label: 'Buscando referencias vinculadas' },
  { porcentaje: 30,  label: 'Verificando en CrossRef y Unpaywall' },
  { porcentaje: 85,  label: 'Indexando fragmentos del paper' },
  { porcentaje: 100, label: 'Verificación completada' },
]

export default function ProgresoAuditoria({ progreso, fase = 'extraccion' }) {
  const { porcentaje, mensaje_progreso, estado, citas_encontradas, error } = progreso

  const etapas = fase === 'verificacion' ? ETAPAS_VERIFICACION : ETAPAS_EXTRACCION

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
        {etapas.map((etapa, i) => {
          const completada = porcentaje >= etapa.porcentaje
          const activa = porcentaje < etapa.porcentaje &&
            (i === 0 || porcentaje >= etapas[i - 1].porcentaje)

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
                border: activa ? '2px solid var(--accent)' : 'none',
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

      {/* Completado fase extracción */}
      {estado === 'listo_extraccion' && (
        <div style={{
          padding: '1rem',
          background: 'var(--accent-subtle)',
          border: '1px solid rgba(59,130,246,0.2)',
          borderRadius: 'var(--radius-md)',
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          animation: 'fadeIn 0.3s ease forwards',
        }}>
          <span style={{ fontSize: '1.5rem' }}>📋</span>
          <div>
            <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--accent)' }}>
              Extracción completada
            </div>
            {citas_encontradas !== null && (
              <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
                {citas_encontradas} citas detectadas — selecciona cuáles verificar
              </div>
            )}
          </div>
        </div>
      )}

      {/* Completado total */}
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
            <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--success)' }}>
              Verificación completada
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
              Pipeline listo — puedes iniciar la auditoría semántica en la pestaña Motor
            </div>
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

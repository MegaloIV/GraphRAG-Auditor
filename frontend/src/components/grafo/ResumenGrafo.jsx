import Badge from '../ui/Badge'

export default function ResumenGrafo({ datos }) {
  const {
    total_nodos,
    nodos_autores,
    nodos_referencias,
    nodos_citas,
    total_relaciones,
    densidad_promedio,
    grafo_robusto,
    citas_vinculadas,
    advertencia_densidad,
    error,
  } = datos

  if (error) {
    return (
      <div style={{
        padding: '1rem',
        background: 'var(--error-subtle)',
        border: '1px solid rgba(239, 68, 68, 0.2)',
        borderRadius: 'var(--radius-md)',
        fontSize: '0.85rem',
        color: 'var(--error)',
      }}>
        ✕ {error}
      </div>
    )
  }

  return (
    <div style={{ animation: 'fadeIn 0.3s ease forwards' }}>

      {/* Estado del grafo */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
        padding: '1rem',
        background: grafo_robusto ? 'var(--success-subtle)' : 'var(--warning-subtle)',
        border: `1px solid ${grafo_robusto ? 'rgba(16, 185, 129, 0.2)' : 'rgba(245, 158, 11, 0.2)'}`,
        borderRadius: 'var(--radius-md)',
        marginBottom: '1.25rem',
      }}>
        <span style={{ fontSize: '1.5rem' }}>
          {grafo_robusto ? '🕸️' : '⚠️'}
        </span>
        <div>
          <div style={{
            fontSize: '0.9rem',
            fontWeight: 600,
            color: grafo_robusto ? 'var(--success)' : 'var(--warning)',
          }}>
            {grafo_robusto ? 'Grafo robusto' : 'Grafo con baja densidad'}
          </div>
          <div style={{
            fontSize: '0.78rem',
            color: 'var(--text-secondary)',
            marginTop: '2px',
          }}>
            {densidad_promedio} relaciones/nodo
            {grafo_robusto ? ' — por encima del mínimo recomendado' : ''}
          </div>
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <Badge
            texto={grafo_robusto ? 'Robusto' : 'Bajo'}
            variante={grafo_robusto ? 'success' : 'warning'}
          />
        </div>
      </div>

      {/* Advertencia densidad */}
      {advertencia_densidad && (
        <div style={{
          padding: '0.75rem 1rem',
          background: 'var(--warning-subtle)',
          border: '1px solid rgba(245, 158, 11, 0.2)',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.82rem',
          color: 'var(--warning)',
          marginBottom: '1.25rem',
        }}>
          {advertencia_densidad}
        </div>
      )}

      {/* Grid de métricas */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(2, 1fr)',
        gap: '0.75rem',
        marginBottom: '1.25rem',
      }}>
        <MetricaGrafo icono="⬡"  label="Total nodos"      valor={total_nodos} />
        <MetricaGrafo icono="↔"  label="Total relaciones"  valor={total_relaciones} />
        <MetricaGrafo icono="👤" label="Autores"            valor={nodos_autores} />
        <MetricaGrafo icono="📚" label="Referencias"        valor={nodos_referencias} />
        <MetricaGrafo icono="💬" label="Citas"              valor={nodos_citas} />
        <MetricaGrafo icono="🔗" label="Citas vinculadas"   valor={citas_vinculadas ?? 0} />
        <MetricaGrafo icono="~"  label="Densidad"           valor={`${densidad_promedio} r/n`} mono />
      </div>
    </div>
  )
}

function MetricaGrafo({ icono, label, valor, mono }) {
  return (
    <div style={{
      padding: '0.875rem',
      background: 'var(--bg-surface-2)',
      borderRadius: 'var(--radius-md)',
      display: 'flex',
      alignItems: 'center',
      gap: '0.75rem',
    }}>
      <div style={{
        width: '32px',
        height: '32px',
        background: 'var(--accent-subtle)',
        borderRadius: 'var(--radius-sm)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: 'var(--accent)',
        fontSize: '0.9rem',
        flexShrink: 0,
      }}>
        {icono}
      </div>
      <div>
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
          color: 'var(--text-primary)',
          fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)',
        }}>
          {valor}
        </div>
      </div>
    </div>
  )
}
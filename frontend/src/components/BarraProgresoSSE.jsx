export default function BarraProgresoSSE({ progreso, conectado }) {
  const porcentaje = progreso?.porcentaje ?? 0
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>
          {progreso?.mensaje_progreso || 'Conectando…'}
        </span>
        <strong style={{ fontSize: 20 }}>{porcentaje}%</strong>
      </div>
      <div className="barra-pista">
        <div className="barra-relleno" style={{ width: `${porcentaje}%` }} />
      </div>
      {!conectado && progreso && (
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
          Conexión en vivo interrumpida — actualizando por sondeo…
        </span>
      )}
    </div>
  )
}

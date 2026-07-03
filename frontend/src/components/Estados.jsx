import { AlertTriangle, Inbox } from 'lucide-react'

export function EstadoCarga({ mensaje = 'Cargando…' }) {
  return (
    <div className="estado-centro">
      <div className="spinner" />
      <p>{mensaje}</p>
    </div>
  )
}

export function EstadoVacio({ titulo, detalle, children }) {
  return (
    <div className="estado-centro">
      <Inbox size={34} strokeWidth={1.4} />
      <h3>{titulo}</h3>
      {detalle && <p>{detalle}</p>}
      {children}
    </div>
  )
}

// Muestra el ErrorResponse del backend con su accion_sugerida.
export function EstadoError({ error, onReintentar }) {
  return (
    <div className="estado-centro">
      <AlertTriangle size={34} strokeWidth={1.4} color="var(--refutes)" />
      <h3>{error?.mensaje || 'Ocurrió un error.'}</h3>
      {error?.accion && <p>{error.accion}</p>}
      {onReintentar && (
        <button className="btn btn-contorno" onClick={onReintentar}>
          Reintentar
        </button>
      )}
    </div>
  )
}

export function ErrorInline({ error }) {
  if (!error) return null
  return (
    <div className="aviso-error">
      {error.mensaje}
      {error.accion ? ` — ${error.accion}` : ''}
    </div>
  )
}

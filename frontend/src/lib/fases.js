// Fases del flujo del usuario (D11). El desbloqueo depende del `estado` real
// del backend (EstadoIngesta), no de lógica propia del frontend.
export const FASES = [
  { clave: 'progreso',  nombre: 'Carga' },
  { clave: 'revision',  nombre: 'Revisión' },
  { clave: 'verificar', nombre: 'Verificación' },
  { clave: 'grafo',     nombre: 'Grafo' },
  { clave: 'auditoria', nombre: 'Auditoría' },
  { clave: 'cierre',    nombre: 'Cierre' },
]

// Índice (0-based sobre FASES) de la última fase desbloqueada según el estado
// que reporta el backend.
export function faseMaxima(estado) {
  switch (estado) {
    case 'pendiente':
    case 'procesando':
      return 0
    case 'revision_pendiente':
      return 1
    case 'listo_extraccion':
    case 'verificando':
      return 2
    case 'completado':
      return 5
    default:
      return 0
  }
}

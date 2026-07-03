import { useEffect } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import BarraProgresoSSE from '../components/BarraProgresoSSE'
import { EstadoError } from '../components/Estados'
import { useSSEProgreso } from '../hooks/useSSEProgreso'

// Fase ① Carga: progreso en tiempo real de la extracción (SSE).
// Al terminar (revision_pendiente) lleva a la revisión humana.
export default function PaginaProgreso() {
  const { documentoId, refrescarEstado } = useOutletContext()
  const { progreso, conectado } = useSSEProgreso(documentoId)
  const navigate = useNavigate()

  useEffect(() => {
    if (!progreso) return
    if (['revision_pendiente', 'listo_extraccion', 'verificando', 'completado'].includes(progreso.estado)) {
      refrescarEstado()
    }
  }, [progreso, refrescarEstado])

  if (progreso?.estado === 'error') {
    return (
      <EstadoError
        error={{ mensaje: progreso.error || 'La extracción falló.', accion: 'Vuelve a cargar el documento desde el inicio.' }}
        onReintentar={() => navigate('/')}
      />
    )
  }

  const terminada = progreso && progreso.estado !== 'procesando' && progreso.estado !== 'pendiente'

  return (
    <div style={{ maxWidth: 640, margin: '40px auto', display: 'flex', flexDirection: 'column', gap: 24 }}>
      <header>
        <h2>Procesando tu documento</h2>
        <p style={{ color: 'var(--text-secondary)', marginTop: 6 }}>
          Extraemos el texto, las referencias APA y las citas, y construimos el grafo.
          Después podrás revisar y corregir todo antes de continuar.
        </p>
      </header>

      <div className="tarjeta tarjeta-pad">
        <BarraProgresoSSE progreso={progreso} conectado={conectado} />
      </div>

      {terminada && (
        <div style={{ textAlign: 'center' }}>
          <p style={{ color: 'var(--text-secondary)', marginBottom: 14 }}>
            {progreso.citas_encontradas
              ? `Se detectaron ${progreso.citas_encontradas} citas.`
              : 'Extracción completada.'}
          </p>
          <button className="btn btn-primario btn-grande" onClick={() => navigate(`/doc/${documentoId}/revision`)}>
            Continuar a la revisión →
          </button>
        </div>
      )}
    </div>
  )
}

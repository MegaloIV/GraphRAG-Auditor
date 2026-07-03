import { useCallback, useEffect, useState } from 'react'
import { Outlet, useLocation, useParams } from 'react-router-dom'
import TopBar from '../components/TopBar'
import RielFases from '../components/RielFases'
import { FASES } from '../lib/fases'
import { EstadoCarga, EstadoError } from '../components/Estados'
import { ingestaAPI } from '../api/client'
import { obtenerDocumento } from '../store/documentoStore'

// Workspace de documento único (D11): top bar + riel de fases + fase activa.
// El estado del documento se lee del backend y gobierna el gating del riel.
export default function WorkspaceDocumento() {
  const { documentoId } = useParams()
  const location = useLocation()
  const [estado, setEstado] = useState(null)
  const [error, setError] = useState(null)

  const docLocal = obtenerDocumento(documentoId)
  const faseActiva = FASES.map((f) => f.clave).find((c) =>
    location.pathname.endsWith(`/${c}`)
  )

  const refrescarEstado = useCallback(async () => {
    try {
      const progreso = await ingestaAPI.obtenerEstado(documentoId)
      setEstado(progreso.estado)
      setError(null)
      return progreso.estado
    } catch {
      setError({
        mensaje: 'No se pudo obtener el estado del documento.',
        accion: 'Verifica que el backend esté corriendo y que el ID sea válido.',
      })
      return null
    }
  }, [documentoId])

  useEffect(() => {
    refrescarEstado()
  }, [refrescarEstado])

  return (
    <div>
      <TopBar nombreDocumento={docLocal?.nombreArchivo || documentoId} />
      <div className="workspace">
        <RielFases documentoId={documentoId} estado={estado} faseActiva={faseActiva} />
        <main className="contenido-fase">
          {error ? (
            <EstadoError error={error} onReintentar={refrescarEstado} />
          ) : !estado ? (
            <EstadoCarga mensaje="Consultando estado del documento…" />
          ) : (
            <Outlet context={{ documentoId, estado, refrescarEstado, docLocal }} />
          )}
        </main>
      </div>
    </div>
  )
}

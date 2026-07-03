import { useCallback, useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Play, Download } from 'lucide-react'
import TarjetaMetrica from '../../components/TarjetaMetrica'
import BadgeVeredicto from '../../components/BadgeVeredicto'
import { EstadoCarga, EstadoVacio, ErrorInline } from '../../components/Estados'
import { auditoriaAPI } from '../../api/client'
import { descargarBlob, num } from '../../lib/formato'

// /admin/ragas — calidad interna del sistema (sin ground truth). Separada de
// la evaluación contra experto (regla dura de no mezclar).
export default function PaginaRagas() {
  const { documentoId } = useOutletContext()
  const [metricas, setMetricas] = useState(null)
  const [veredictos, setVeredictos] = useState(null)
  const [cargando, setCargando] = useState(false)
  const [evaluando, setEvaluando] = useState(false)
  const [error, setError] = useState(null)

  const cargar = useCallback(async () => {
    if (!documentoId) return
    setCargando(true)
    setError(null)
    const [m, v] = await Promise.allSettled([
      auditoriaAPI.verMetricas(documentoId),
      auditoriaAPI.verVeredictos(documentoId),
    ])
    setMetricas(m.status === 'fulfilled' ? m.value.data : null)
    setVeredictos(v.status === 'fulfilled' ? v.value.data : null)
    setCargando(false)
  }, [documentoId])

  useEffect(() => { cargar() }, [cargar])

  const evaluar = async () => {
    setEvaluando(true)
    setError(null)
    try {
      await auditoriaAPI.evaluarRagas(documentoId)
      await cargar()
    } catch (e) {
      setError(e)
    } finally {
      setEvaluando(false)
    }
  }

  const exportar = async () => {
    try {
      const { data } = await auditoriaAPI.exportarInformeExcel(documentoId)
      descargarBlob(data, `informe_auditoria_${documentoId}.xlsx`)
    } catch (e) {
      setError(e)
    }
  }

  if (!documentoId) {
    return <EstadoVacio titulo="Selecciona un documento" detalle="Elige un documento en el selector de arriba." />
  }
  if (cargando) return <EstadoCarga />
  if (evaluando) return <EstadoCarga mensaje="Evaluando con RAGAS… puede tardar varios minutos (LLM por cada cita)." />

  const evaluado = metricas && metricas.total_evaluadas > 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button className="btn btn-contorno" onClick={exportar} disabled={!veredictos}>
          <Download size={14} /> Exportar informe
        </button>
        <button className="btn btn-primario" onClick={evaluar} disabled={!veredictos}>
          <Play size={14} /> {evaluado ? 'Re-evaluar RAGAS' : 'Lanzar evaluación RAGAS'}
        </button>
      </div>

      <ErrorInline error={error} />

      {!veredictos && (
        <EstadoVacio
          titulo="El documento aún no fue auditado"
          detalle="RAGAS evalúa la calidad de los veredictos: primero ejecuta la auditoría en el flujo del documento."
        />
      )}

      {veredictos && !evaluado && (
        <EstadoVacio
          titulo="Sin métricas RAGAS todavía"
          detalle="Lanza la evaluación para calcular faithfulness, answer relevancy y context precision por cita."
        />
      )}

      {evaluado && (
        <>
          <div className="grid-metricas">
            <TarjetaMetrica etiqueta="Citas evaluadas" valor={metricas.total_evaluadas} />
            <TarjetaMetrica
              etiqueta="Faithfulness"
              valor={num(metricas.faithfulness_promedio)}
              detalle="¿La justificación se ancla en la evidencia?"
            />
            <TarjetaMetrica
              etiqueta="Answer relevancy"
              valor={num(metricas.answer_relevancy_promedio)}
              detalle="¿La respuesta es relevante al claim?"
            />
            <TarjetaMetrica
              etiqueta="Context precision"
              valor={num(metricas.context_precision_promedio)}
              detalle="¿La evidencia recuperada es pertinente?"
            />
          </div>

          {veredictos && (
            <div className="tabla-scroll tarjeta">
              <table className="tabla">
                <thead>
                  <tr>
                    <th>Cita</th>
                    <th>Veredicto</th>
                    <th>Faithfulness</th>
                    <th>Relevancy</th>
                    <th>Precision</th>
                  </tr>
                </thead>
                <tbody>
                  {veredictos.veredictos.map((v) => (
                    <tr key={v.cita_id}>
                      <td className="texto-doc">{v.texto_cita}</td>
                      <td><BadgeVeredicto veredicto={v.veredicto} soloCodigo /></td>
                      <td>{num(v.faithfulness)}</td>
                      <td>{num(v.answer_relevancy)}</td>
                      <td>{num(v.context_precision)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

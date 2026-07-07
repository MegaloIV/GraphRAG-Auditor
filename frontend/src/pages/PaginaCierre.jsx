import { useCallback, useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Download, AlertTriangle } from 'lucide-react'
import TarjetaMetrica from '../components/TarjetaMetrica'
import CoherenciaFuentes from '../components/CoherenciaFuentes'
import { EstadoCarga, ErrorInline } from '../components/Estados'
import { auditoriaAPI, grafoAPI } from '../api/client'
import { descargarBlob } from '../lib/formato'

function ListaAlertas({ titulo, alertas }) {
  if (!alertas?.length) return null
  return (
    <section className="tarjeta tarjeta-pad" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <h3 style={{ fontSize: 15, display: 'flex', alignItems: 'center', gap: 8 }}>
        <AlertTriangle size={16} color="var(--warn)" />
        {titulo} ({alertas.length})
      </h3>
      {alertas.map((a, i) => (
        <div key={i} style={{ fontSize: 13.5, paddingLeft: 24 }}>
          <span className="texto-doc">“{a.elemento || a.texto_cita}”</span>
          <span style={{ color: 'var(--text-faint)' }}>
            {' '}— {a.descripcion || a.razon_no_verificable} ({a.ubicacion || `pág. ${a.pagina}`})
          </span>
        </div>
      ))}
    </section>
  )
}

// Fase ⑥ Cierre (para el jurado): resumen de veredictos, alertas
// estructurales y descarga del informe Excel de auditoría.
export default function PaginaCierre() {
  const { documentoId, docLocal } = useOutletContext()
  const [veredictos, setVeredictos] = useState(null)
  const [alertas, setAlertas] = useState(null)
  const [alucinaciones, setAlucinaciones] = useState(null)
  const [resumen, setResumen] = useState(null)
  const [cargando, setCargando] = useState(true)
  const [descargando, setDescargando] = useState(false)
  const [error, setError] = useState(null)

  const cargar = useCallback(async () => {
    setCargando(true)
    const [v, a, h, r] = await Promise.allSettled([
      auditoriaAPI.verVeredictos(documentoId),
      auditoriaAPI.verAlertas(documentoId),
      auditoriaAPI.verAlertasAlucinaciones(documentoId),
      grafoAPI.verResumen(documentoId),
    ])
    if (v.status === 'fulfilled') setVeredictos(v.value.data)
    if (a.status === 'fulfilled') setAlertas(a.value.data)
    if (h.status === 'fulfilled') setAlucinaciones(h.value.data)
    if (r.status === 'fulfilled') setResumen(r.value.data)
    setCargando(false)
  }, [documentoId])

  useEffect(() => { cargar() }, [cargar])

  const descargar = async () => {
    setDescargando(true)
    setError(null)
    try {
      const { data } = await auditoriaAPI.exportarInformeExcel(documentoId)
      descargarBlob(data, `informe_auditoria_${documentoId}.xlsx`)
    } catch (e) {
      setError(e)
    } finally {
      setDescargando(false)
    }
  }

  if (cargando) return <EstadoCarga mensaje="Preparando el resumen…" />

  const auditada = veredictos && veredictos.total_citas > 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 900, margin: '0 auto' }}>
      <header style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <h2 style={{ fontSize: 26 }}>Auditoría completada</h2>
        <p style={{ color: 'var(--text-secondary)' }}>
          Resumen final de <strong>{docLocal?.nombreArchivo || 'tu documento'}</strong>.
        </p>
      </header>

      {auditada ? (
        <div className="grid-metricas">
          <TarjetaMetrica etiqueta="Citas auditadas" valor={veredictos.total_citas} />
          <TarjetaMetrica etiqueta="Respaldadas" valor={veredictos.supports} color="var(--supports)" />
          <TarjetaMetrica etiqueta="Refutadas" valor={veredictos.refutes} color="var(--refutes)" />
          <TarjetaMetrica etiqueta="Sin evidencia" valor={veredictos.no_info} color="var(--noinfo)" />
        </div>
      ) : (
        <div className="aviso-warn">
          Aún no se ejecutó la auditoría: el informe incluirá solo la estructura del documento.
        </div>
      )}

      {resumen && (
        <div className="grid-metricas">
          <TarjetaMetrica etiqueta="Referencias" valor={resumen.nodos_referencias} />
          <TarjetaMetrica etiqueta="Citas" valor={resumen.nodos_citas} />
          <TarjetaMetrica etiqueta="Citas vinculadas" valor={resumen.citas_vinculadas} />
          <TarjetaMetrica
            etiqueta="Densidad del grafo"
            valor={resumen.densidad_promedio}
            detalle={resumen.grafo_robusto ? 'grafo robusto' : 'baja densidad'}
            color={resumen.grafo_robusto ? 'var(--supports)' : 'var(--warn)'}
          />
        </div>
      )}

      <ListaAlertas titulo="Citas sin referencia" alertas={alertas?.citas_sin_referencia} />
      <ListaAlertas titulo="Referencias sin citar" alertas={alertas?.referencias_sin_citar} />
      <ListaAlertas titulo="Citas no verificables (posibles alucinaciones)" alertas={alucinaciones?.alertas} />

      <CoherenciaFuentes documentoId={documentoId} auditada={!!auditada} />

      <ErrorInline error={error} />

      <div style={{ textAlign: 'center', paddingBottom: 30 }}>
        <button className="btn btn-primario btn-grande" onClick={descargar} disabled={descargando || !auditada}>
          <Download size={18} />
          {descargando ? 'Generando informe…' : 'Descargar informe (Excel)'}
        </button>
        {!auditada && (
          <p style={{ fontSize: 13, color: 'var(--text-faint)', marginTop: 8 }}>
            Ejecuta la auditoría para habilitar el informe.
          </p>
        )}
      </div>
    </div>
  )
}

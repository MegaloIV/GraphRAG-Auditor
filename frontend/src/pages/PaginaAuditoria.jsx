import { useState, useEffect, useRef } from 'react'
import Navbar from '../components/ui/Navbar'
import Card from '../components/ui/Card'
import ProgresoAuditoria from '../components/ingesta/ProgresoAuditoria'
import CitasVerificacion from '../components/citas/CitasVerificacion'
import ListaReferencias from '../components/grafo/ListaReferencias'
import GrafoVisual from '../components/grafo/GrafoVisual'
import EstadoMotor from '../components/recuperacion/EstadoMotor'
import ListaVeredictos from '../components/auditoria/ListaVeredictos'
import AlertasInconsistencias from '../components/auditoria/AlertasInconsistencias'
import AlertasAlucinaciones from '../components/auditoria/AlertasAlucinaciones'
import MetricasRagas from '../components/auditoria/MetricasRagas'
import { grafoAPI, recuperacionAPI, auditoriaAPI } from '../api/client'

// Tabs que sólo aparecen una vez que las citas están disponibles
const TABS_EXTRACCION = [
  { id: 'citas',       label: 'Citas',       icono: '💬' },
  { id: 'referencias', label: 'Referencias', icono: '📚' },
  { id: 'grafo',       label: 'Grafo',       icono: '⬡'  },
  { id: 'motor',       label: 'Motor',       icono: '🔍' },
  { id: 'veredictos',  label: 'Veredictos',  icono: '✓'  },
  { id: 'alertas',     label: 'Alertas',     icono: '⚠️' },
  { id: 'ragas',       label: 'Informe',     icono: '📋' },
]

export default function PaginaAuditoria({ documentoId, onVolver }) {
  const [tabActiva, setTabActiva] = useState('citas')

  // ── Estado pipeline ──────────────────────────────────────
  const [progreso, setProgreso] = useState({
    documento_id: documentoId,
    estado: 'procesando',
    porcentaje: 0,
    mensaje_progreso: 'Iniciando extracción...',
    citas_encontradas: null,
    error: null,
  })
  const [fase, setFase] = useState('extraccion')

  // ── Datos grafo ──────────────────────────────────────────
  const [referencias, setReferencias]   = useState(null)
  const [citas, setCitas]               = useState(null)
  const [grafoVisual, setGrafoVisual]   = useState(null)

  // ── EP-003: motor ────────────────────────────────────────
  const [estadoMotor, setEstadoMotor] = useState(null)

  // ── EP-004: auditoría ────────────────────────────────────
  const [auditando, setAuditando]                   = useState(false)
  const [auditoriaData, setAuditoriaData]           = useState(null)
  const [alertas, setAlertas]                       = useState(null)
  const [alertasAlucinacion, setAlertasAlucinacion] = useState(null)
  const [errorAuditoria, setErrorAuditoria]         = useState(null)
  const [metricasRagas, setMetricasRagas]           = useState(null)

  const esRef = useRef(null)

  // ── Carga de datos al finalizar fase 1 ───────────────────
  const cargarDatosFase1 = async () => {
    try {
      const [refRes, citasRes, grafoRes] = await Promise.all([
        grafoAPI.verReferencias(documentoId),
        grafoAPI.verCitas(documentoId),
        grafoAPI.verGrafoVisual(documentoId),
      ])
      setReferencias(refRes.data)
      setCitas(citasRes.data)
      setGrafoVisual(grafoRes.data)
    } catch (err) {
      console.error('Error cargando datos fase 1:', err)
    }
  }

  // ── Carga completa tras fase 2 ───────────────────────────
  const cargarDatosFase2 = async () => {
    try {
      const [refRes, citasRes, motorRes, grafoRes] = await Promise.all([
        grafoAPI.verReferencias(documentoId),
        grafoAPI.verCitas(documentoId),
        recuperacionAPI.estadoMotor(documentoId),
        grafoAPI.verGrafoVisual(documentoId),
      ])
      setReferencias(refRes.data)
      setCitas(citasRes.data)
      setEstadoMotor(motorRes.data)
      setGrafoVisual(grafoRes.data)
    } catch (err) {
      console.error('Error cargando datos fase 2:', err)
    }

    try {
      const [verRes, alertasRes, alucinRes] = await Promise.all([
        auditoriaAPI.verVeredictos(documentoId),
        auditoriaAPI.verAlertas(documentoId),
        auditoriaAPI.verAlertasAlucinaciones(documentoId),
      ])
      setAuditoriaData(verRes.data)
      setAlertas(alertasRes.data)
      setAlertasAlucinacion(alucinRes.data)
    } catch { /* no hay veredictos aún */ }

    try {
      const metRes = await auditoriaAPI.verMetricas(documentoId)
      setMetricasRagas(metRes.data)
    } catch { /* no hay métricas aún */ }
  }

  // ── Recargar solo referencias (tras subida manual) ───────
  const recargarReferencias = async () => {
    try {
      const refRes = await grafoAPI.verReferencias(documentoId)
      setReferencias(refRes.data)
    } catch (err) {
      console.error('Error recargando referencias:', err)
    }
  }

  // ── Recargar el grafo visual (tras auditar o evaluar RAGAS) ───
  const recargarGrafoVisual = async () => {
    try {
      const grafoRes = await grafoAPI.verGrafoVisual(documentoId)
      setGrafoVisual(grafoRes.data)
    } catch (err) {
      console.error('Error recargando grafo visual:', err)
    }
  }

  // ── SSE ──────────────────────────────────────────────────
  const conectarSSE = () => {
    esRef.current?.close()
    const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'
    const es = new EventSource(`${API_BASE}/ingesta/${documentoId}/progreso`)
    esRef.current = es

    es.onmessage = async (event) => {
      try {
        const datos = JSON.parse(event.data)
        setProgreso(datos)
        if (datos.estado === 'listo_extraccion') {
          es.close()
          await cargarDatosFase1()
          setTabActiva('citas')
        } else if (datos.estado === 'completado') {
          es.close()
          await cargarDatosFase2()
        } else if (datos.estado === 'error') {
          es.close()
        }
      } catch (err) {
        console.error('Error parseando SSE:', err)
      }
    }

    es.onerror = () => {
      setProgreso(prev => ({
        ...prev,
        estado: 'error',
        mensaje_progreso: 'Se perdió la conexión con el servidor.',
        error: 'No se pudo mantener la conexión en tiempo real.',
      }))
      es.close()
    }
  }

  useEffect(() => {
    conectarSSE()
    return () => esRef.current?.close()
  }, [documentoId])

  // ── El usuario confirma la verificación ─────────────────
  const handleVerificacionIniciada = () => {
    setFase('verificacion')
    conectarSSE()
  }

  // ── Auditoría semántica ──────────────────────────────────
  const iniciarAuditoria = async () => {
    setAuditando(true)
    setErrorAuditoria(null)
    try {
      const res = await auditoriaAPI.auditar(documentoId)
      setAuditoriaData(res.data)
      const [alertasRes, alucinRes, grafoRes] = await Promise.all([
        auditoriaAPI.verAlertas(documentoId),
        auditoriaAPI.verAlertasAlucinaciones(documentoId),
        grafoAPI.verGrafoVisual(documentoId),
      ])
      setAlertas(alertasRes.data)
      setAlertasAlucinacion(alucinRes.data)
      setGrafoVisual(grafoRes.data)
      setTabActiva('veredictos')
    } catch (err) {
      setErrorAuditoria(err.mensaje || 'Error al auditar el documento.')
    } finally {
      setAuditando(false)
    }
  }

  const estado = progreso.estado
  const extrayendo = estado === 'procesando'
  const fase1Done  = ['listo_extraccion', 'verificando', 'completado'].includes(estado)
  const fase2Done  = estado === 'completado'
  const auditoriaDone = !!auditoriaData

  const tabDeshabilitada = (tabId) => {
    if (['referencias', 'grafo'].includes(tabId)) return !fase1Done
    if (tabId === 'motor') return !fase2Done
    if (['veredictos', 'alertas', 'ragas'].includes(tabId)) return !auditoriaDone
    return false
  }

  // ── Render ───────────────────────────────────────────────
  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-base)' }}>
      <Navbar mostrarVolver onVolver={onVolver} />

      <div style={{ maxWidth: '900px', margin: '0 auto', padding: '2rem 1.5rem' }}>

        {/* Header */}
        <div style={{ marginBottom: '1.5rem', animation: 'fadeIn 0.3s ease forwards' }}>
          <h2 style={{ fontSize: '1.4rem', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '0.25rem' }}>
            Panel de Auditoría
          </h2>
          <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
            ID: {documentoId}
          </div>
        </div>

        {/* ── Mientras extrae: pantalla de progreso (sin tabs) ── */}
        {extrayendo && (
          <Card titulo="Extrayendo documento" subtitulo="Analizando citas y referencias..." icono="⏳">
            <ProgresoAuditoria progreso={progreso} fase="extraccion" />
          </Card>
        )}

        {/* ── Error en fase 1 (antes de que aparezcan citas) ── */}
        {estado === 'error' && !fase1Done && (
          <Card titulo="Error en la extracción" icono="✕">
            <div style={{ padding: '1rem', background: 'var(--error-subtle)', borderRadius: 'var(--radius-md)', fontSize: '0.85rem', color: 'var(--error)' }}>
              {progreso.error || 'Ocurrió un error durante el procesamiento.'}
            </div>
          </Card>
        )}

        {/* ── Una vez que hay citas: tabs normales ── */}
        {fase1Done && (
          <>
            {/* Banner de verificación en curso (encima de las tabs) */}
            {estado === 'verificando' && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: '0.75rem',
                padding: '0.75rem 1rem', marginBottom: '1rem',
                background: 'var(--accent-subtle)',
                border: '1px solid rgba(59,130,246,0.2)',
                borderRadius: 'var(--radius-md)',
                fontSize: '0.83rem', color: 'var(--accent)', fontWeight: 500,
                animation: 'fadeIn 0.3s ease forwards',
              }}>
                <div style={{
                  width: 16, height: 16, flexShrink: 0,
                  border: '2px solid rgba(59,130,246,0.3)',
                  borderTop: '2px solid var(--accent)',
                  borderRadius: '50%', animation: 'spin 1s linear infinite',
                }} />
                {progreso.mensaje_progreso || 'Verificando referencias en CrossRef y Unpaywall...'}
              </div>
            )}

            {/* Tabs */}
            <div style={{
              display: 'flex', gap: '0.25rem', marginBottom: '1.5rem',
              background: 'var(--bg-surface)', padding: '0.25rem',
              borderRadius: 'var(--radius-md)', border: '1px solid var(--border)',
              width: 'fit-content', flexWrap: 'wrap',
            }}>
              {TABS_EXTRACCION.map(tab => {
                const deshabilitada = tabDeshabilitada(tab.id)
                return (
                  <button
                    key={tab.id}
                    onClick={() => !deshabilitada && setTabActiva(tab.id)}
                    style={{
                      padding: '0.5rem 1rem', borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      background: tabActiva === tab.id ? 'var(--accent)' : 'transparent',
                      color: tabActiva === tab.id ? 'white' : deshabilitada ? 'var(--text-muted)' : 'var(--text-secondary)',
                      fontSize: '0.82rem', fontWeight: tabActiva === tab.id ? 600 : 400,
                      cursor: deshabilitada ? 'not-allowed' : 'pointer',
                      fontFamily: 'var(--font-sans)',
                      display: 'flex', alignItems: 'center', gap: '0.4rem',
                      transition: 'all 0.2s ease', opacity: deshabilitada ? 0.5 : 1,
                    }}
                  >
                    {tab.icono} {tab.label}
                  </button>
                )
              })}
            </div>

            {/* ── Citas: hub central de verificación ── */}
            {tabActiva === 'citas' && citas && (
              <Card
                titulo="Citas detectadas"
                subtitulo={`${citas.total_citas} citas detectadas`}
                icono="💬"
              >
                <CitasVerificacion
                  documentoId={documentoId}
                  citas={citas.citas}
                  referencias={referencias?.referencias ?? []}
                  estadoPipeline={estado}
                  onVerificacionIniciada={handleVerificacionIniciada}
                />
              </Card>
            )}

            {tabActiva === 'citas' && !citas && <Spinner />}

            {tabActiva === 'referencias' && referencias && (
              <Card
                titulo="Referencias bibliográficas"
                subtitulo={`${referencias.total_referencias} referencias detectadas`}
                icono="📚"
              >
                <ListaReferencias
                  documentoId={documentoId}
                  referencias={referencias.referencias}
                  total={referencias.total_referencias}
                  advertencia={referencias.advertencia}
                  onRecargarReferencias={recargarReferencias}
                />
              </Card>
            )}

            {tabActiva === 'grafo' && (
              <Card titulo="Grafo de conocimiento" subtitulo="Se actualiza al verificar y auditar" icono="⬡">
                <GrafoVisual data={grafoVisual} />
              </Card>
            )}

            {tabActiva === 'motor' && (
              <Card titulo="Motor de búsqueda semántica" subtitulo="Preparación del motor GraphRAG" icono="🔍">
                {estadoMotor ? (
                  <>
                    <EstadoMotor datos={estadoMotor} onAuditar={iniciarAuditoria} auditando={auditando} />
                    {errorAuditoria && (
                      <div style={{ marginTop: '1rem', padding: '0.875rem', background: 'var(--error-subtle)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 'var(--radius-md)', fontSize: '0.83rem', color: 'var(--error)' }}>
                        ✕ {errorAuditoria}
                      </div>
                    )}
                  </>
                ) : <Spinner />}
              </Card>
            )}

            {tabActiva === 'veredictos' && auditoriaData && (
              <Card titulo="Veredictos de auditoría" subtitulo={`${auditoriaData.total_citas} citas auditadas`} icono="✓">
                <ListaVeredictos
                  veredictos={auditoriaData.veredictos}
                  supports={auditoriaData.supports}
                  refutes={auditoriaData.refutes}
                  no_info={auditoriaData.no_info}
                  advertencia={auditoriaData.advertencia}
                />
              </Card>
            )}

            {tabActiva === 'alertas' && alertas && (
              <>
                <Card titulo="Inconsistencias estructurales" subtitulo="Citas sin referencia y referencias sin citar" icono="⚠️" style={{ marginBottom: '1rem' }}>
                  <AlertasInconsistencias datos={alertas} />
                </Card>
                {alertasAlucinacion && (
                  <Card titulo="Alertas de verificación" subtitulo="Citas que el sistema no pudo verificar" icono="🔴">
                    <AlertasAlucinaciones datos={alertasAlucinacion} />
                  </Card>
                )}
              </>
            )}

            {tabActiva === 'ragas' && auditoriaDone && (
              <Card titulo="Informe de auditoría" subtitulo="Evaluación semántica y exportación del informe" icono="📋">
                <MetricasRagas
                  documentoId={documentoId}
                  metricas={metricasRagas}
                  onMetricasActualizadas={(data) => { setMetricasRagas(data); recargarGrafoVisual() }}
                />
              </Card>
            )}

            {/* Spinner para Referencias mientras carga */}
            {fase1Done && tabActiva === 'referencias' && !referencias && <Spinner />}
          </>
        )}
      </div>
    </div>
  )
}

function Spinner() {
  return (
    <div style={{
      width: 32, height: 32,
      border: '3px solid var(--border)',
      borderTop: '3px solid var(--accent)',
      borderRadius: '50%', margin: '2rem auto',
      animation: 'spin 1s linear infinite',
    }} />
  )
}

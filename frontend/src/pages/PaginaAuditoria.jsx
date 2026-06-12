import { useState, useEffect, useRef } from 'react'
import Navbar from '../components/ui/Navbar'
import Card from '../components/ui/Card'
import ProgresoAuditoria from '../components/ingesta/ProgresoAuditoria'
import SeleccionVerificacion from '../components/ingesta/SeleccionVerificacion'
import ListaReferencias from '../components/grafo/ListaReferencias'
import ListaCitas from '../components/grafo/ListaCitas'
import ResumenGrafo from '../components/grafo/ResumenGrafo'
import EstadoMotor from '../components/recuperacion/EstadoMotor'
import ListaVeredictos from '../components/auditoria/ListaVeredictos'
import AlertasInconsistencias from '../components/auditoria/AlertasInconsistencias'
import AlertasAlucinaciones from '../components/auditoria/AlertasAlucinaciones'
import MetricasRagas from '../components/auditoria/MetricasRagas'
import { grafoAPI, recuperacionAPI, auditoriaAPI } from '../api/client'

const TABS = [
  { id: 'progreso',    label: 'Progreso',    icono: '⏳' },
  { id: 'referencias', label: 'Referencias', icono: '📚' },
  { id: 'citas',       label: 'Citas',       icono: '💬' },
  { id: 'grafo',       label: 'Grafo',       icono: '⬡'  },
  { id: 'motor',       label: 'Motor',       icono: '🔍' },
  { id: 'veredictos',  label: 'Veredictos',  icono: '✓'  },
  { id: 'alertas',     label: 'Alertas',     icono: '⚠️' },
  { id: 'ragas',       label: 'RAGAS',       icono: '📊' },
]

export default function PaginaAuditoria({ documentoId, onVolver }) {
  const [tabActiva, setTabActiva] = useState('progreso')

  // ── Estado pipeline ──────────────────────────────────────
  const [progreso, setProgreso] = useState({
    documento_id: documentoId,
    estado: 'procesando',
    porcentaje: 0,
    mensaje_progreso: 'Iniciando extracción...',
    citas_encontradas: null,
    error: null,
  })
  // 'extraccion' durante fase 1, 'verificacion' durante fase 2
  const [fase, setFase] = useState('extraccion')

  // ── Datos grafo ──────────────────────────────────────────
  const [referencias, setReferencias]   = useState(null)
  const [citas, setCitas]               = useState(null)
  const [resumenGrafo, setResumenGrafo] = useState(null)

  // ── EP-003: motor ────────────────────────────────────────
  const [estadoMotor, setEstadoMotor] = useState(null)

  // ── EP-004: auditoría ────────────────────────────────────
  const [auditando, setAuditando]                       = useState(false)
  const [auditoriaData, setAuditoriaData]               = useState(null)
  const [alertas, setAlertas]                           = useState(null)
  const [alertasAlucinacion, setAlertasAlucinacion]     = useState(null)
  const [errorAuditoria, setErrorAuditoria]             = useState(null)
  const [metricasRagas, setMetricasRagas]               = useState(null)

  // Ref para poder cerrar / reabrir EventSource
  const esRef = useRef(null)

  // ── Carga de datos fase 1 (refs, citas, grafo) ──────────
  const cargarDatosFase1 = async () => {
    try {
      const [refRes, citasRes, grafoRes] = await Promise.all([
        grafoAPI.verReferencias(documentoId),
        grafoAPI.verCitas(documentoId),
        grafoAPI.verResumen(documentoId),
      ])
      setReferencias(refRes.data)
      setCitas(citasRes.data)
      setResumenGrafo(grafoRes.data)
    } catch (err) {
      console.error('Error cargando datos fase 1:', err)
    }
  }

  // ── Carga completa tras fase 2 (añade motor + veredictos previos) ──
  const cargarDatosFase2 = async () => {
    try {
      const motorRes = await recuperacionAPI.estadoMotor(documentoId)
      setEstadoMotor(motorRes.data)
    } catch (err) {
      console.error('Error cargando motor:', err)
    }

    // Recargar refs para reflejar nivel_confianza actualizado post-verificación
    try {
      const [refRes, citasRes] = await Promise.all([
        grafoAPI.verReferencias(documentoId),
        grafoAPI.verCitas(documentoId),
      ])
      setReferencias(refRes.data)
      setCitas(citasRes.data)
    } catch { /* no crítico */ }

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

  // ── Función para conectar (o reconectar) el SSE ──────────
  const conectarSSE = () => {
    if (esRef.current) {
      esRef.current.close()
    }
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

  // ── El usuario confirmó la selección → arrancar fase 2 ──
  const handleVerificacionIniciada = () => {
    setFase('verificacion')
    conectarSSE()
  }

  // ── Iniciar auditoría semántica ──────────────────────────
  const iniciarAuditoria = async () => {
    setAuditando(true)
    setErrorAuditoria(null)
    try {
      const res = await auditoriaAPI.auditar(documentoId)
      setAuditoriaData(res.data)

      const [alertasRes, alucinRes] = await Promise.all([
        auditoriaAPI.verAlertas(documentoId),
        auditoriaAPI.verAlertasAlucinaciones(documentoId),
      ])
      setAlertas(alertasRes.data)
      setAlertasAlucinacion(alucinRes.data)
      setTabActiva('veredictos')
    } catch (err) {
      setErrorAuditoria(err.mensaje || 'Error al auditar el documento.')
    } finally {
      setAuditando(false)
    }
  }

  const estado = progreso.estado
  // Fase 1 terminada cuando la extracción está lista o ya se pasó más allá
  const fase1Done = ['listo_extraccion', 'verificando', 'completado'].includes(estado)
  // Fase 2 terminada cuando el pipeline completo está listo
  const fase2Done = estado === 'completado'
  const auditoriaDone = !!auditoriaData

  const tabDeshabilitada = (tabId) => {
    if (tabId === 'progreso') return false
    if (['referencias', 'citas', 'grafo'].includes(tabId)) return !fase1Done
    if (tabId === 'motor') return !fase2Done
    if (['veredictos', 'alertas', 'ragas'].includes(tabId)) return !auditoriaDone
    return false
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-base)' }}>
      <Navbar mostrarVolver onVolver={onVolver} />

      <div style={{ maxWidth: '900px', margin: '0 auto', padding: '2rem 1.5rem' }}>

        {/* Header */}
        <div style={{ marginBottom: '1.5rem', animation: 'fadeIn 0.3s ease forwards' }}>
          <h2 style={{
            fontSize: '1.4rem',
            fontWeight: 700,
            color: 'var(--text-primary)',
            marginBottom: '0.25rem',
          }}>
            Panel de Auditoría
          </h2>
          <div style={{
            fontSize: '0.78rem',
            color: 'var(--text-muted)',
            fontFamily: 'var(--font-mono)',
          }}>
            ID: {documentoId}
          </div>
        </div>

        {/* Tabs */}
        <div style={{
          display: 'flex',
          gap: '0.25rem',
          marginBottom: '1.5rem',
          background: 'var(--bg-surface)',
          padding: '0.25rem',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border)',
          width: 'fit-content',
          flexWrap: 'wrap',
        }}>
          {TABS.map(tab => {
            const deshabilitada = tabDeshabilitada(tab.id)
            return (
              <button
                key={tab.id}
                onClick={() => !deshabilitada && setTabActiva(tab.id)}
                style={{
                  padding: '0.5rem 1rem',
                  borderRadius: 'var(--radius-sm)',
                  border: 'none',
                  background: tabActiva === tab.id ? 'var(--accent)' : 'transparent',
                  color: tabActiva === tab.id
                    ? 'white'
                    : deshabilitada
                    ? 'var(--text-muted)'
                    : 'var(--text-secondary)',
                  fontSize: '0.82rem',
                  fontWeight: tabActiva === tab.id ? 600 : 400,
                  cursor: deshabilitada ? 'not-allowed' : 'pointer',
                  fontFamily: 'var(--font-sans)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  transition: 'all 0.2s ease',
                  opacity: deshabilitada ? 0.5 : 1,
                }}
              >
                {tab.icono} {tab.label}
              </button>
            )
          })}
        </div>

        {/* ── Contenido tabs ── */}

        {tabActiva === 'progreso' && (
          <>
            {/* Fase 1 corriendo o error */}
            {(estado === 'procesando' || (estado === 'error' && fase === 'extraccion')) && (
              <Card titulo="Extracción del documento" subtitulo="Actualizando en tiempo real" icono="⏳">
                <ProgresoAuditoria progreso={progreso} fase="extraccion" />
              </Card>
            )}

            {/* Fase 1 completada: mostrar selección */}
            {estado === 'listo_extraccion' && citas && referencias && (
              <Card
                titulo="Selecciona las citas a verificar"
                subtitulo="Elige qué citas enviar a CrossRef y Unpaywall"
                icono="🔎"
              >
                <SeleccionVerificacion
                  documentoId={documentoId}
                  citas={citas.citas}
                  referencias={referencias.referencias}
                  onVerificacionIniciada={handleVerificacionIniciada}
                />
              </Card>
            )}

            {/* Fase 2 corriendo */}
            {(estado === 'verificando' || (estado === 'error' && fase === 'verificacion')) && (
              <Card titulo="Verificación externa" subtitulo="CrossRef · Unpaywall · Embeddings" icono="🔗">
                <ProgresoAuditoria progreso={progreso} fase="verificacion" />
              </Card>
            )}

            {/* Todo completado */}
            {estado === 'completado' && (
              <Card titulo="Pipeline completado" subtitulo="Listo para auditar" icono="✅">
                <ProgresoAuditoria progreso={progreso} fase="verificacion" />
              </Card>
            )}

            {/* Fase 1 completada pero datos aún cargando */}
            {estado === 'listo_extraccion' && (!citas || !referencias) && (
              <Card titulo="Extracción completada" subtitulo="Cargando resultados..." icono="⏳">
                <Spinner />
              </Card>
            )}
          </>
        )}

        {tabActiva === 'referencias' && referencias && (
          <Card
            titulo="Referencias bibliográficas"
            subtitulo={`${referencias.total_referencias} referencias detectadas`}
            icono="📚"
          >
            <ListaReferencias
              referencias={referencias.referencias}
              total={referencias.total_referencias}
              advertencia={referencias.advertencia}
            />
          </Card>
        )}

        {tabActiva === 'citas' && citas && (
          <Card
            titulo="Citas en el texto"
            subtitulo={`${citas.total_citas} citas detectadas`}
            icono="💬"
          >
            <ListaCitas
              citas={citas.citas}
              total={citas.total_citas}
              parenteticas={citas.citas_parenteticas}
              narrativas={citas.citas_narrativas}
              advertencia={citas.advertencia}
            />
          </Card>
        )}

        {tabActiva === 'grafo' && resumenGrafo && (
          <Card titulo="Grafo de conocimiento" subtitulo="Estadísticas de Neo4j" icono="⬡">
            <ResumenGrafo datos={resumenGrafo} />
          </Card>
        )}

        {tabActiva === 'motor' && (
          <Card
            titulo="Motor de búsqueda semántica"
            subtitulo="Preparación del motor GraphRAG"
            icono="🔍"
          >
            {estadoMotor ? (
              <>
                <EstadoMotor
                  datos={estadoMotor}
                  onAuditar={iniciarAuditoria}
                  auditando={auditando}
                />
                {errorAuditoria && (
                  <div style={{
                    marginTop: '1rem',
                    padding: '0.875rem',
                    background: 'var(--error-subtle)',
                    border: '1px solid rgba(239,68,68,0.2)',
                    borderRadius: 'var(--radius-md)',
                    fontSize: '0.83rem',
                    color: 'var(--error)',
                  }}>
                    ✕ {errorAuditoria}
                  </div>
                )}
              </>
            ) : (
              <Spinner />
            )}
          </Card>
        )}

        {tabActiva === 'veredictos' && auditoriaData && (
          <Card
            titulo="Veredictos de auditoría"
            subtitulo={`${auditoriaData.total_citas} citas auditadas`}
            icono="✓"
          >
            <ListaVeredictos
              veredictos={auditoriaData.veredictos}
              total={auditoriaData.total_citas}
              validas={auditoriaData.validas}
              dudosas={auditoriaData.dudosas}
              alucinadas={auditoriaData.alucinadas}
              no_verificables={auditoriaData.no_verificables}
              advertencia={auditoriaData.advertencia}
            />
          </Card>
        )}

        {tabActiva === 'alertas' && alertas && (
          <>
            <Card
              titulo="Inconsistencias estructurales"
              subtitulo="Citas sin referencia y referencias sin citar"
              icono="⚠️"
              style={{ marginBottom: '1rem' }}
            >
              <AlertasInconsistencias datos={alertas} />
            </Card>

            {alertasAlucinacion && (
              <Card
                titulo="Alertas de verificación"
                subtitulo="Citas que el sistema no pudo verificar"
                icono="🔴"
              >
                <AlertasAlucinaciones datos={alertasAlucinacion} />
              </Card>
            )}
          </>
        )}

        {tabActiva === 'ragas' && auditoriaDone && (
          <Card
            titulo="Métricas RAGAS"
            subtitulo="Evaluación semántica de la auditoría"
            icono="📊"
          >
            <MetricasRagas
              documentoId={documentoId}
              metricas={metricasRagas}
              onMetricasActualizadas={setMetricasRagas}
            />
          </Card>
        )}

        {/* Spinner genérico para tabs con datos aún cargando */}
        {fase1Done && tabActiva !== 'progreso' &&
          !referencias && !citas && !resumenGrafo && tabActiva !== 'motor' && (
          <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
            <Spinner />
            <div style={{ marginTop: '1rem', fontSize: '0.85rem' }}>Cargando resultados...</div>
          </div>
        )}
      </div>
    </div>
  )
}

function Spinner() {
  return (
    <div style={{
      width: '32px',
      height: '32px',
      border: '3px solid var(--border)',
      borderTop: '3px solid var(--accent)',
      borderRadius: '50%',
      margin: '2rem auto',
      animation: 'spin 1s linear infinite',
    }} />
  )
}

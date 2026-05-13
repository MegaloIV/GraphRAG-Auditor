import { useState, useEffect } from 'react'
import Navbar from '../components/ui/Navbar'
import Card from '../components/ui/Card'
import ProgresoAuditoria from '../components/ingesta/ProgresoAuditoria'
import ListaReferencias from '../components/grafo/ListaReferencias'
import ListaCitas from '../components/grafo/ListaCitas'
import ResumenGrafo from '../components/grafo/ResumenGrafo'
import EstadoMotor from '../components/recuperacion/EstadoMotor'
import ListaVeredictos from '../components/auditoria/ListaVeredictos'
import AlertasInconsistencias from '../components/auditoria/AlertasInconsistencias'
import AlertasAlucinaciones from '../components/auditoria/AlertasAlucinaciones'
import { grafoAPI, recuperacionAPI, auditoriaAPI } from '../api/client'

const TABS = [
  { id: 'progreso',      label: 'Progreso',    icono: '⏳' },
  { id: 'referencias',   label: 'Referencias', icono: '📚' },
  { id: 'citas',         label: 'Citas',       icono: '💬' },
  { id: 'grafo',         label: 'Grafo',       icono: '⬡'  },
  { id: 'motor',         label: 'Motor',       icono: '🔍' },
  { id: 'veredictos',    label: 'Veredictos',  icono: '✓'  },
  { id: 'alertas',       label: 'Alertas',     icono: '⚠️' },
]

export default function PaginaAuditoria({ documentoId, onVolver }) {
  const [tabActiva, setTabActiva] = useState('progreso')

  // ── Estado pipeline ──────────────────────────────────────
  const [progreso, setProgreso] = useState({
    documento_id: documentoId,
    estado: 'procesando',
    porcentaje: 0,
    mensaje_progreso: 'Iniciando auditoría...',
    citas_encontradas: null,
    error: null,
  })

  // ── Datos grafo ──────────────────────────────────────────
  const [referencias, setReferencias]   = useState(null)
  const [citas, setCitas]               = useState(null)
  const [resumenGrafo, setResumenGrafo] = useState(null)

  // ── EP-003: motor ────────────────────────────────────────
  const [estadoMotor, setEstadoMotor] = useState(null)

  // ── EP-004: auditoría ────────────────────────────────────
  const [auditando, setAuditando]           = useState(false)
  const [auditoriaData, setAuditoriaData]   = useState(null)
  const [alertas, setAlertas]               = useState(null)
  const [alertasAlucinacion, setAlertasAlucinacion] = useState(null)
  const [errorAuditoria, setErrorAuditoria] = useState(null)

  // ── SSE + carga inicial ──────────────────────────────────
  useEffect(() => {
    const cargarResultados = async () => {
      try {
        const [refRes, citasRes, grafoRes, motorRes] = await Promise.all([
          grafoAPI.verReferencias(documentoId),
          grafoAPI.verCitas(documentoId),
          grafoAPI.verResumen(documentoId),
          recuperacionAPI.estadoMotor(documentoId),
        ])
        setReferencias(refRes.data)
        setCitas(citasRes.data)
        setResumenGrafo(grafoRes.data)
        setEstadoMotor(motorRes.data)
      } catch (err) {
        console.error('Error cargando resultados:', err)
      }

      // Si ya hay veredictos calculados, cargarlos
      try {
        const [verRes, alertasRes, alucinRes] = await Promise.all([
          auditoriaAPI.verVeredictos(documentoId),
          auditoriaAPI.verAlertas(documentoId),
          auditoriaAPI.verAlertasAlucinaciones(documentoId),
        ])
        setAuditoriaData(verRes.data)
        setAlertas(alertasRes.data)
        setAlertasAlucinacion(alucinRes.data)
      } catch {
        // No hay veredictos aún — es normal
      }
    }

    const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'
    const eventSource = new EventSource(`${API_BASE}/ingesta/${documentoId}/progreso`)

    eventSource.onmessage = (event) => {
      try {
        const datos = JSON.parse(event.data)
        setProgreso(datos)
        if (datos.estado === 'completado') {
          eventSource.close()
          cargarResultados()
        } else if (datos.estado === 'error') {
          eventSource.close()
        }
      } catch (err) {
        console.error('Error parseando SSE:', err)
      }
    }

    eventSource.onerror = () => {
      setProgreso(prev => ({
        ...prev,
        estado: 'error',
        mensaje_progreso: 'Se perdió la conexión con el servidor.',
        error: 'No se pudo mantener la conexión en tiempo real.',
      }))
      eventSource.close()
    }

    return () => eventSource.close()
  }, [documentoId])

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

  const pipelineDone  = progreso.estado === 'completado'
  const auditoriaDone = !!auditoriaData

  // Tabs deshabilitadas según estado
  const tabDeshabilitada = (tabId) => {
    if (tabId === 'progreso') return false
    if (!pipelineDone) return true
    if (['veredictos', 'alertas'].includes(tabId) && !auditoriaDone) return true
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
          <Card titulo="Estado del pipeline" subtitulo="Actualizando en tiempo real" icono="⏳">
            <ProgresoAuditoria progreso={progreso} />
          </Card>
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
            subtitulo="HU-007: preparación del motor GraphRAG"
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

        {/* Spinner genérico para tabs cargando */}
        {pipelineDone && tabActiva !== 'progreso' &&
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
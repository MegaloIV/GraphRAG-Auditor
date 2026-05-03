import { useState, useEffect, useRef } from 'react'
import Navbar from '../components/ui/Navbar'
import Card from '../components/ui/Card'
import ProgresoAuditoria from '../components/ingesta/ProgresoAuditoria'
import ListaReferencias from '../components/grafo/ListaReferencias'
import ListaCitas from '../components/grafo/ListaCitas'
import ResumenGrafo from '../components/grafo/ResumenGrafo'
import { ingestaAPI, grafoAPI } from '../api/client'

const TABS = [
  { id: 'progreso',    label: 'Progreso',    icono: '⏳' },
  { id: 'referencias', label: 'Referencias', icono: '📚' },
  { id: 'citas',       label: 'Citas',       icono: '💬' },
  { id: 'grafo',       label: 'Grafo',       icono: '⬡'  },
]

export default function PaginaAuditoria({ documentoId, onVolver }) {
  const [tabActiva, setTabActiva] = useState('progreso')
  const [progreso, setProgreso] = useState({
    documento_id: documentoId,
    estado: 'procesando',
    porcentaje: 0,
    mensaje_progreso: 'Iniciando auditoría...',
    citas_encontradas: null,
    error: null,
  })
  const [referencias, setReferencias] = useState(null)
  const [citas, setCitas] = useState(null)
  const [resumenGrafo, setResumenGrafo] = useState(null)
  const intervalRef = useRef(null)
  const intentosFallidosRef = useRef(0)

  useEffect(() => {
    const cargarResultados = async () => {
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
        console.error(err)
      }
    }

    const polling = async () => {
      try {
        const response = await ingestaAPI.verProgreso(documentoId)
        const datos = response.data
        intentosFallidosRef.current = 0
        setProgreso(datos)

        if (datos.estado === 'completado') {
          clearInterval(intervalRef.current)
          cargarResultados()
        } else if (datos.estado === 'error') {
          clearInterval(intervalRef.current)
        }
      } catch {
        intentosFallidosRef.current += 1
        // Solo asumir completado después de 5 intentos fallidos (~12.5s)
        if (intentosFallidosRef.current >= 5) {
          clearInterval(intervalRef.current)
          setProgreso(prev => ({
            ...prev,
            estado: 'completado',
            porcentaje: 100,
            mensaje_progreso: 'Auditoría completada.',
          }))
          cargarResultados()
        }
      }
    }

    const timer = setTimeout(() => {
      intervalRef.current = setInterval(polling, 2500)
      polling()
    }, 1000)

    return () => {
      clearTimeout(timer)
      clearInterval(intervalRef.current)
    }
  }, [documentoId])

  const auditoriaDone = progreso.estado === 'completado'

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-base)' }}>
      <Navbar mostrarVolver onVolver={onVolver} />

      <div style={{
        maxWidth: '900px',
        margin: '0 auto',
        padding: '2rem 1.5rem',
      }}>

        {/* Header */}
        <div style={{
          marginBottom: '1.5rem',
          animation: 'fadeIn 0.3s ease forwards',
        }}>
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
        }}>
          {TABS.map(tab => {
            const deshabilitada = !auditoriaDone && tab.id !== 'progreso'
            return (
              <button
                key={tab.id}
                onClick={() => !deshabilitada && setTabActiva(tab.id)}
                style={{
                  padding: '0.5rem 1rem',
                  borderRadius: 'var(--radius-sm)',
                  border: 'none',
                  background: tabActiva === tab.id
                    ? 'var(--accent)'
                    : 'transparent',
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

        {/* Contenido de tabs */}
        {tabActiva === 'progreso' && (
          <Card
            titulo="Estado del pipeline"
            subtitulo="Actualización cada 2.5 segundos"
            icono="⏳"
          >
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
          <Card
            titulo="Grafo de conocimiento"
            subtitulo="Estadísticas de Neo4j"
            icono="⬡"
          >
            <ResumenGrafo datos={resumenGrafo} />
          </Card>
        )}

        {/* Cargando resultados */}
        {auditoriaDone && tabActiva !== 'progreso' &&
          !referencias && !citas && !resumenGrafo && (
          <div style={{
            textAlign: 'center',
            padding: '3rem',
            color: 'var(--text-muted)',
          }}>
            <div style={{
              width: '32px',
              height: '32px',
              border: '3px solid var(--border)',
              borderTop: '3px solid var(--accent)',
              borderRadius: '50%',
              margin: '0 auto 1rem',
              animation: 'spin 1s linear infinite',
            }} />
            Cargando resultados...
          </div>
        )}
      </div>
    </div>
  )
}
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { Gavel, ChevronDown, ChevronUp } from 'lucide-react'
import BadgeVeredicto from '../components/BadgeVeredicto'
import TarjetaMetrica from '../components/TarjetaMetrica'
import { EstadoCarga, EstadoVacio, ErrorInline } from '../components/Estados'
import { auditoriaAPI } from '../api/client'
import { pct } from '../lib/formato'

const SECCIONES = {
  resumen: 'resumen',
  introduccion: 'introducción',
  trabajos_relacionados: 'trabajos relacionados',
  metodos: 'métodos',
  resultados: 'resultados',
  discusion: 'discusión',
  conclusion: 'conclusión',
}

// El juez debe transcribir literalmente del paper y el backend coteja esa
// transcripción contra el fragmento recuperado. Separar los avisos permite
// destacarlos: son lo que el revisor tiene que comprobar a mano.
function partirAvisos(justificacion) {
  const i = (justificacion || '').indexOf('[')
  if (i === -1) return [justificacion, null]
  return [justificacion.slice(0, i).trim(), justificacion.slice(i).replace(/^\[|\]$/g, '')]
}

function FilaVeredicto({ v }) {
  const [abierto, setAbierto] = useState(false)
  return (
    <div className="tarjeta" style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <button
        onClick={() => setAbierto(!abierto)}
        style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', padding: 0, color: 'inherit', width: '100%', flexWrap: 'wrap' }}
      >
        <BadgeVeredicto veredicto={v.veredicto} />
        <span className="texto-doc" style={{ flex: 1, minWidth: 160, fontWeight: 600 }}>{v.texto_cita}</span>
        <span className="badge badge-neutro">pág. {v.pagina}</span>
        {v.similitud > 0 && <span className="badge badge-neutro">similitud {pct(v.similitud)}</span>}
        {v.seccion_evidencia === 'trabajos_relacionados' && (
          <span className="badge badge-alerta" title="La evidencia sale de la sección donde el paper resume trabajo ajeno.">
            evidencia de terceros
          </span>
        )}
        {abierto ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>

      {abierto && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13.5, paddingTop: 4, borderTop: '1px solid var(--border)' }}>
          {v.fragmento_oracion && (
            <p className="texto-doc" style={{ color: 'var(--text-secondary)' }}>
              <b style={{ fontFamily: 'var(--font-ui)' }}>Aseveración: </b>“{v.fragmento_oracion}”
            </p>
          )}
          {(() => {
            const [texto, aviso] = partirAvisos(v.justificacion)
            return (
              <>
                <p><b>Justificación:</b> {texto}</p>
                {aviso && (
                  <p style={{
                    margin: 0, padding: '8px 12px', borderRadius: 'var(--radius-sm)',
                    background: 'var(--bg-sunken)', borderLeft: '3px solid var(--warning, #d97706)',
                    fontSize: 13,
                  }}>
                    <b>Revisa a mano: </b>{aviso}
                  </p>
                )}
              </>
            )
          })()}

          {v.cita_textual && (
            <div>
              <b>Frase del paper que lo sostiene</b>
              <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
                {' '}· verificada literalmente contra el texto recuperado
                {v.seccion_evidencia && SECCIONES[v.seccion_evidencia]
                  ? ` · sección: ${SECCIONES[v.seccion_evidencia]}`
                  : ''}
              </span>
              <blockquote
                className="texto-doc"
                style={{
                  margin: '6px 0 0', padding: '10px 14px', background: 'var(--bg-sunken)',
                  borderLeft: '3px solid var(--ok, #16a34a)', borderRadius: 'var(--radius-sm)',
                }}
              >
                “{v.cita_textual}”
              </blockquote>
            </div>
          )}
          {(v.fragmento_evidencia_es || v.fragmento_evidencia) && (
            <details>
              <summary style={{ cursor: 'pointer', fontSize: 13 }}>
                Ver el pasaje completo recuperado
              </summary>
            <blockquote
              className="texto-doc"
              style={{ margin: 0, padding: '10px 14px', background: 'var(--bg-sunken)', borderLeft: '3px solid var(--accent)', borderRadius: 'var(--radius-sm)' }}
              title={v.fragmento_evidencia_es ? 'Traducción al español; el original se conserva en el sistema.' : undefined}
            >
              “{v.fragmento_evidencia_es || v.fragmento_evidencia}”
              <span style={{ fontFamily: 'var(--font-ui)', fontSize: 12, color: 'var(--text-faint)' }}>
                {v.pagina_paper != null && <>{' '}— paper, pág. {v.pagina_paper}</>}
                {v.fragmento_evidencia_es && <>{' '}· traducido</>}
              </span>
            </blockquote>
            </details>
          )}
          {v.titulo_referencia && (
            <span style={{ color: 'var(--text-secondary)' }}>
              <b>Fuente:</b> {v.titulo_referencia}
              {v.anio_referencia ? ` (${v.anio_referencia})` : ''}
              {v.doi_referencia ? ` · ${v.doi_referencia}` : ''}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

// Fase ⑤ Auditoría: lanza la auditoría LLM y muestra el veredicto por cita.
export default function PaginaAuditoria() {
  const { documentoId } = useOutletContext()
  const navigate = useNavigate()
  const [resultado, setResultado] = useState(null)
  const [cargando, setCargando] = useState(true)
  const [auditando, setAuditando] = useState(false)
  const [error, setError] = useState(null)
  const [filtro, setFiltro] = useState('TODOS')

  const cargarVeredictos = useCallback(async () => {
    setCargando(true)
    try {
      const { data } = await auditoriaAPI.verVeredictos(documentoId)
      setResultado(data.total_citas > 0 ? data : null)
    } catch {
      setResultado(null) // aún no auditado
    } finally {
      setCargando(false)
    }
  }, [documentoId])

  useEffect(() => { cargarVeredictos() }, [cargarVeredictos])

  const auditar = async () => {
    setAuditando(true)
    setError(null)
    try {
      const { data } = await auditoriaAPI.auditar(documentoId)
      setResultado(data)
    } catch (e) {
      setError(e)
    } finally {
      setAuditando(false)
    }
  }

  if (cargando) return <EstadoCarga mensaje="Consultando veredictos…" />

  if (auditando) {
    return (
      <EstadoCarga mensaje="Auditando citas con el LLM… esto puede tardar varios minutos. No cierres esta pestaña." />
    )
  }

  if (!resultado) {
    return (
      <div style={{ maxWidth: 620, margin: '0 auto' }}>
        <EstadoVacio
          titulo="Aún no hay veredictos"
          detalle="La auditoría contrasta cada cita con la evidencia recuperada de su fuente y emite un veredicto por cita."
        >
          <ErrorInline error={error} />
          <button className="btn btn-primario btn-grande" onClick={auditar}>
            <Gavel size={17} /> Iniciar auditoría
          </button>
        </EstadoVacio>
      </div>
    )
  }

  const visibles = filtro === 'TODOS'
    ? resultado.veredictos
    : resultado.veredictos.filter((v) => v.veredicto === filtro)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <header style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <h2>Veredictos de la auditoría</h2>
          <p style={{ color: 'var(--text-secondary)', marginTop: 4, fontSize: 14 }}>
            Cada cita fue contrastada con la evidencia de su fuente.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-contorno" onClick={auditar}>
            <Gavel size={14} /> Re-auditar
          </button>
          <button className="btn btn-primario" onClick={() => navigate(`/doc/${documentoId}/cierre`)}>
            Ir al cierre →
          </button>
        </div>
      </header>

      <ErrorInline error={error} />

      <div className="grid-metricas">
        <TarjetaMetrica etiqueta="Citas auditadas" valor={resultado.total_citas} />
        <TarjetaMetrica etiqueta="Respaldadas" valor={resultado.supports} color="var(--supports)" />
        <TarjetaMetrica etiqueta="Refutadas" valor={resultado.refutes} color="var(--refutes)" />
        <TarjetaMetrica etiqueta="Sin evidencia" valor={resultado.no_info} color="var(--noinfo)" />
      </div>

      {resultado.advertencia && <div className="aviso-warn">{resultado.advertencia}</div>}

      <div className="pestanas">
        {['TODOS', 'SUPPORTS', 'REFUTES', 'NO_INFO'].map((f) => (
          <button key={f} className={`pestana ${filtro === f ? 'activa' : ''}`} onClick={() => setFiltro(f)}>
            {f === 'TODOS' ? 'Todas' : f}
          </button>
        ))}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {visibles.map((v) => <FilaVeredicto key={v.cita_id} v={v} />)}
        {visibles.length === 0 && <EstadoVacio titulo="No hay citas con este veredicto" />}
      </div>
    </div>
  )
}

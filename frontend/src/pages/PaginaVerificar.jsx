import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { ShieldCheck, Upload } from 'lucide-react'
import BarraProgresoSSE from '../components/BarraProgresoSSE'
import { EstadoCarga, EstadoError, ErrorInline } from '../components/Estados'
import { grafoAPI, ingestaAPI } from '../api/client'
import { useSSEProgreso } from '../hooks/useSSEProgreso'
import { formatearRef } from '../lib/formato'

const NIVELES = {
  texto_completo: { texto: 'texto completo', clase: 'badge-supports' },
  solo_metadatos: { texto: 'solo metadatos', clase: 'badge-warn' },
  manual:         { texto: 'manual',         clase: 'badge-accent' },
  no_encontrado:  { texto: 'no encontrada',  clase: 'badge-refutes' },
}

function FilaReferencia({ referencia, marcada, onMarcar, onSubirPaper, subiendo }) {
  const inputRef = useRef(null)
  const nivel = NIVELES[referencia.nivel_confianza]
  return (
    <div className="tarjeta" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px' }}>
      <input
        type="checkbox"
        checked={marcada}
        onChange={(e) => onMarcar(referencia.referencia_id, e.target.checked)}
        style={{ width: 16, height: 16 }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <strong>{formatearRef(referencia)}</strong>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {referencia.titulo}
        </div>
      </div>
      {nivel && <span className={`badge ${nivel.clase}`}>{nivel.texto}</span>}
      {referencia.nivel_confianza === 'no_encontrado' && (
        <>
          <button className="btn" onClick={() => inputRef.current?.click()} disabled={subiendo}>
            <Upload size={14} /> {subiendo ? 'Subiendo…' : 'Subir PDF'}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf"
            hidden
            onChange={(e) => {
              const archivo = e.target.files?.[0]
              if (archivo) onSubirPaper(referencia.referencia_id, archivo)
              e.target.value = ''
            }}
          />
        </>
      )}
    </div>
  )
}

// Fase ③ Verificación externa: el usuario elige qué referencias verificar en
// CrossRef/Unpaywall; las no encontradas admiten subir el paper manualmente.
export default function PaginaVerificar() {
  const { documentoId, estado, refrescarEstado } = useOutletContext()
  const navigate = useNavigate()

  const [referencias, setReferencias] = useState(null)
  const [marcadas, setMarcadas] = useState(new Set())
  const [errorCarga, setErrorCarga] = useState(null)
  const [errorAccion, setErrorAccion] = useState(null)
  const [verificando, setVerificando] = useState(estado === 'verificando')
  const [subiendoRef, setSubiendoRef] = useState(null)
  const { progreso, conectado } = useSSEProgreso(verificando ? documentoId : null)

  const cargar = useCallback(async () => {
    setErrorCarga(null)
    try {
      const { data } = await grafoAPI.verReferencias(documentoId)
      setReferencias(data.referencias)
      setMarcadas(new Set(
        data.referencias
          .filter((r) => !r.nivel_confianza) // aún sin verificar
          .map((r) => r.referencia_id)
      ))
    } catch (e) {
      setErrorCarga(e)
    }
  }, [documentoId])

  useEffect(() => { cargar() }, [cargar])

  useEffect(() => {
    if (progreso?.estado === 'completado') {
      refrescarEstado()
      cargar()
      setVerificando(false)
    }
    if (progreso?.estado === 'error') setVerificando(false)
  }, [progreso, refrescarEstado, cargar])

  const marcar = (refId, activa) => {
    setMarcadas((prev) => {
      const s = new Set(prev)
      if (activa) s.add(refId)
      else s.delete(refId)
      return s
    })
  }

  const iniciar = async () => {
    setErrorAccion(null)
    try {
      await ingestaAPI.iniciarVerificacion(documentoId, [...marcadas])
      setVerificando(true)
    } catch (e) {
      setErrorAccion(e)
    }
  }

  const subirPaper = async (refId, archivo) => {
    setSubiendoRef(refId)
    setErrorAccion(null)
    try {
      await grafoAPI.subirPaperManual(documentoId, refId, archivo)
      await cargar()
    } catch (e) {
      setErrorAccion(e)
    } finally {
      setSubiendoRef(null)
    }
  }

  if (errorCarga) return <EstadoError error={errorCarga} onReintentar={cargar} />
  if (!referencias) return <EstadoCarga mensaje="Cargando referencias…" />

  if (verificando) {
    return (
      <div style={{ maxWidth: 640, margin: '40px auto', display: 'flex', flexDirection: 'column', gap: 20 }}>
        <h2>Verificando referencias</h2>
        <p style={{ color: 'var(--text-secondary)' }}>
          Consultamos CrossRef y Unpaywall, descargamos el texto de los papers
          disponibles y los indexamos para la auditoría.
        </p>
        <div className="tarjeta tarjeta-pad">
          <BarraProgresoSSE progreso={progreso} conectado={conectado} />
        </div>
        {progreso?.estado === 'error' && <ErrorInline error={{ mensaje: progreso.error }} />}
      </div>
    )
  }

  const completada = estado === 'completado'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 860, margin: '0 auto' }}>
      <header style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <h2>Verifica las fuentes</h2>
          <p style={{ color: 'var(--text-secondary)', marginTop: 4, fontSize: 14 }}>
            Selecciona las referencias a contrastar con CrossRef y Unpaywall. Si una
            no se encuentra, podrás subir su PDF manualmente.
          </p>
        </div>
        <button className="btn btn-primario" onClick={iniciar} disabled={marcadas.size === 0}>
          <ShieldCheck size={15} />
          Verificar {marcadas.size} referencia{marcadas.size === 1 ? '' : 's'}
        </button>
      </header>

      <ErrorInline error={errorAccion} />

      {completada && (
        <div className="aviso-warn">
          Ya hay una verificación completada. Puedes verificar más referencias o{' '}
          <a style={{ cursor: 'pointer' }} onClick={() => navigate(`/doc/${documentoId}/grafo`)}>
            continuar al grafo →
          </a>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {referencias.map((ref) => (
          <FilaReferencia
            key={ref.referencia_id}
            referencia={ref}
            marcada={marcadas.has(ref.referencia_id)}
            onMarcar={marcar}
            onSubirPaper={subirPaper}
            subiendo={subiendoRef === ref.referencia_id}
          />
        ))}
      </div>
    </div>
  )
}

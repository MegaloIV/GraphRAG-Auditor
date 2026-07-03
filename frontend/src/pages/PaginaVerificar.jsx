import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { ShieldCheck, Upload, X, BookUp2 } from 'lucide-react'
import BarraProgresoSSE from '../components/BarraProgresoSSE'
import { EstadoCarga, EstadoError, ErrorInline } from '../components/Estados'
import { grafoAPI, ingestaAPI } from '../api/client'
import { useSSEProgreso } from '../hooks/useSSEProgreso'
import { formatearRef } from '../lib/formato'

// Valores reales de nivel_confianza que escribe el backend.
const NIVELES = {
  texto_completo: { texto: 'texto completo', clase: 'badge-supports' },
  zotero:         { texto: 'PDF de Zotero',  clase: 'badge-supports' },
  manual:         { texto: 'PDF manual',     clase: 'badge-supports' },
  cache:          { texto: 'en caché',       clase: 'badge-accent' },
  abstract:       { texto: 'solo abstract',  clase: 'badge-warn' },
  sin_texto:      { texto: 'DOI sin texto',  clase: 'badge-warn' },
  no_encontrado:  { texto: 'sin paper',      clase: 'badge-refutes' },
}

const CON_PAPER = new Set(['texto_completo', 'zotero', 'manual', 'cache', 'abstract'])

function FilaReferencia({ referencia, marcada, onMarcar, onSubirPaper, onQuitarPaper, ocupada }) {
  const inputRef = useRef(null)
  const nivel = NIVELES[referencia.nivel_confianza]
  const tienePaper = CON_PAPER.has(referencia.nivel_confianza)
  const sinDoi = !referencia.doi

  return (
    <div className="tarjeta" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', flexWrap: 'wrap' }}>
      <input
        type="checkbox"
        checked={marcada}
        disabled={sinDoi}
        title={sinDoi ? 'Sin DOI: impórtala desde Zotero o sube su PDF' : undefined}
        onChange={(e) => onMarcar(referencia.referencia_id, e.target.checked)}
        style={{ width: 16, height: 16 }}
      />
      <div style={{ flex: 1, minWidth: 200 }}>
        <strong>{formatearRef(referencia)}</strong>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {referencia.titulo}
        </div>
      </div>
      {sinDoi && <span className="badge badge-neutro" title="La verificación automática requiere DOI">sin DOI</span>}
      {nivel && <span className={`badge ${nivel.clase}`}>{nivel.texto}</span>}
      <button
        className="btn"
        onClick={() => inputRef.current?.click()}
        disabled={ocupada}
        title={tienePaper ? 'Sustituir el PDF asociado a esta referencia' : 'Subir el PDF de esta referencia'}
      >
        <Upload size={14} /> {ocupada ? 'Procesando…' : tienePaper ? 'Reemplazar' : 'Subir PDF'}
      </button>
      {tienePaper && (
        <button
          className="btn-icono"
          title="Quitar el paper asociado (vuelve a 'sin paper')"
          disabled={ocupada}
          onClick={() => onQuitarPaper(referencia.referencia_id)}
        >
          <X size={14} />
        </button>
      )}
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
    </div>
  )
}

// Fase ③ Verificación externa. La verificación automática usa SOLO el DOI
// (sin búsqueda por título/autor, que traía papers equivocados). Las
// referencias sin DOI se resuelven importando la colección de Zotero
// (.ris o .zip con PDFs) o subiendo el PDF a mano.
export default function PaginaVerificar() {
  const { documentoId, estado, refrescarEstado } = useOutletContext()
  const navigate = useNavigate()

  const [referencias, setReferencias] = useState(null)
  const [marcadas, setMarcadas] = useState(new Set())
  const [errorCarga, setErrorCarga] = useState(null)
  const [errorAccion, setErrorAccion] = useState(null)
  const [enProceso, setEnProceso] = useState(estado === 'verificando')
  const [tituloProceso, setTituloProceso] = useState('Verificando referencias')
  const [refOcupada, setRefOcupada] = useState(null)
  const [resumenZotero, setResumenZotero] = useState(null)
  const zoteroInputRef = useRef(null)
  const { progreso, conectado } = useSSEProgreso(enProceso ? documentoId : null)

  const cargar = useCallback(async () => {
    setErrorCarga(null)
    try {
      const { data } = await grafoAPI.verReferencias(documentoId)
      setReferencias(data.referencias)
      setMarcadas(new Set(
        data.referencias
          .filter((r) => !r.nivel_confianza && r.doi) // pendientes y verificables
          .map((r) => r.referencia_id)
      ))
    } catch (e) {
      setErrorCarga(e)
    }
  }, [documentoId])

  useEffect(() => { cargar() }, [cargar])

  useEffect(() => {
    if (!enProceso || !progreso) return
    if (progreso.estado === 'completado' || progreso.estado === 'listo_extraccion') {
      refrescarEstado()
      cargar()
      setEnProceso(false)
      // Si fue una importación de Zotero, mostrar su resumen.
      ingestaAPI.resultadoZotero(documentoId)
        .then((res) => setResumenZotero(res.data))
        .catch(() => {})
    }
    if (progreso.estado === 'error') setEnProceso(false)
  }, [progreso, enProceso, refrescarEstado, cargar, documentoId])

  const marcar = (refId, activa) => {
    setMarcadas((prev) => {
      const s = new Set(prev)
      if (activa) s.add(refId)
      else s.delete(refId)
      return s
    })
  }

  const iniciarVerificacion = async () => {
    setErrorAccion(null)
    setResumenZotero(null)
    try {
      await ingestaAPI.iniciarVerificacion(documentoId, [...marcadas])
      setTituloProceso('Verificando referencias')
      setEnProceso(true)
    } catch (e) {
      setErrorAccion(e)
    }
  }

  const importarZotero = async (archivo) => {
    setErrorAccion(null)
    setResumenZotero(null)
    try {
      await ingestaAPI.importarZotero(documentoId, archivo)
      setTituloProceso('Importando colección de Zotero')
      setEnProceso(true)
    } catch (e) {
      setErrorAccion(e)
    }
  }

  const subirPaper = async (refId, archivo) => {
    setRefOcupada(refId)
    setErrorAccion(null)
    try {
      await grafoAPI.subirPaperManual(documentoId, refId, archivo)
      await cargar()
    } catch (e) {
      setErrorAccion(e)
    } finally {
      setRefOcupada(null)
    }
  }

  const quitarPaper = async (refId) => {
    if (!window.confirm('¿Quitar el paper asociado? Los veredictos previos de sus citas quedarán obsoletos.')) return
    setRefOcupada(refId)
    setErrorAccion(null)
    try {
      await grafoAPI.quitarPaper(documentoId, refId)
      await cargar()
    } catch (e) {
      setErrorAccion(e)
    } finally {
      setRefOcupada(null)
    }
  }

  if (errorCarga) return <EstadoError error={errorCarga} onReintentar={cargar} />
  if (!referencias) return <EstadoCarga mensaje="Cargando referencias…" />

  if (enProceso) {
    return (
      <div style={{ maxWidth: 640, margin: '40px auto', display: 'flex', flexDirection: 'column', gap: 20 }}>
        <h2>{tituloProceso}</h2>
        <p style={{ color: 'var(--text-secondary)' }}>
          Asociamos cada referencia con su paper y lo indexamos para la auditoría.
          Esto puede tardar unos minutos.
        </p>
        <div className="tarjeta tarjeta-pad">
          <BarraProgresoSSE progreso={progreso} conectado={conectado} />
        </div>
        {progreso?.estado === 'error' && <ErrorInline error={{ mensaje: progreso.error }} />}
      </div>
    )
  }

  const completada = estado === 'completado'
  const sinDoi = referencias.filter((r) => !r.doi).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 900, margin: '0 auto' }}>
      <header style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 280 }}>
          <h2>Asocia las fuentes</h2>
          <p style={{ color: 'var(--text-secondary)', marginTop: 4, fontSize: 14 }}>
            La verificación automática usa <strong>solo el DOI</strong> de cada referencia
            (sin adivinar por título). Para las que no tienen DOI
            {sinDoi > 0 ? ` (${sinDoi})` : ''}, importa tu colección de Zotero o sube el PDF.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn-contorno" onClick={() => zoteroInputRef.current?.click()}>
            <BookUp2 size={15} />
            Importar de Zotero
          </button>
          <button className="btn btn-primario" onClick={iniciarVerificacion} disabled={marcadas.size === 0}>
            <ShieldCheck size={15} />
            Verificar {marcadas.size} por DOI
          </button>
        </div>
        <input
          ref={zoteroInputRef}
          type="file"
          accept=".ris,.zip"
          hidden
          onChange={(e) => {
            const archivo = e.target.files?.[0]
            if (archivo) importarZotero(archivo)
            e.target.value = ''
          }}
        />
      </header>

      <ErrorInline error={errorAccion} />

      {resumenZotero && (
        <div className="tarjeta tarjeta-pad" style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13.5 }}>
          <strong>Resultado de la importación de Zotero</strong>
          <span>
            {resumenZotero.emparejadas_por_doi + resumenZotero.emparejadas_por_titulo} de{' '}
            {resumenZotero.entradas_ris} entradas emparejadas
            {' '}({resumenZotero.emparejadas_por_doi} por DOI, {resumenZotero.emparejadas_por_titulo} por título/autor) ·{' '}
            {resumenZotero.con_pdf_zotero} PDFs desde Zotero · {resumenZotero.descargadas_unpaywall} descargados por DOI
          </span>
          {resumenZotero.sin_texto?.length > 0 && (
            <span style={{ color: 'var(--warn)' }}>
              Sin texto disponible ({resumenZotero.sin_texto.length}): {resumenZotero.sin_texto.join(' · ')}
            </span>
          )}
          {resumenZotero.no_emparejadas?.length > 0 && (
            <span style={{ color: 'var(--text-secondary)' }}>
              Entradas del RIS sin referencia equivalente ({resumenZotero.no_emparejadas.length}):{' '}
              {resumenZotero.no_emparejadas.join(' · ')}
            </span>
          )}
        </div>
      )}

      {completada && (
        <div className="aviso-warn">
          Ya hay una verificación completada. Puedes asociar más papers o{' '}
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
            onQuitarPaper={quitarPaper}
            ocupada={refOcupada === ref.referencia_id}
          />
        ))}
      </div>
    </div>
  )
}

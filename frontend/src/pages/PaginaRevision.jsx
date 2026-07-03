import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { Plus, RefreshCcw, CheckCheck, BookMarked } from 'lucide-react'
import VisorPDF from '../components/VisorPDF'
import CartillaCita from '../components/CartillaCita'
import CartillaReferencia from '../components/CartillaReferencia'
import { EstadoCarga, EstadoError, EstadoVacio, ErrorInline } from '../components/Estados'
import { ingestaAPI, grafoAPI } from '../api/client'

// Fase ② Revisión humana (D4): PDF real a la izquierda, cartillas editables a
// la derecha. Clic en una cartilla → el visor salta al punto exacto de la cita.
export default function PaginaRevision() {
  const { documentoId, estado, refrescarEstado } = useOutletContext()
  const navigate = useNavigate()

  const [citas, setCitas] = useState(null)
  const [referencias, setReferencias] = useState(null)
  const [ubicaciones, setUbicaciones] = useState(null)
  const [paginaReferencias, setPaginaReferencias] = useState(null)
  const [salto, setSalto] = useState(null)
  const [errorCarga, setErrorCarga] = useState(null)
  const [errorAccion, setErrorAccion] = useState(null)

  const [pestana, setPestana] = useState('citas')
  const [soloSinVincular, setSoloSinVincular] = useState(false)
  const [foco, setFoco] = useState(null)
  const [confirmando, setConfirmando] = useState(false)
  const [reVinculando, setReVinculando] = useState(false)
  const cartillasRef = useRef({})

  // Clic en una cita subrayada del PDF → mostrar y enfocar su cartilla.
  const seleccionarDesdePDF = useCallback((citaId) => {
    setPestana('citas')
    setSoloSinVincular(false) // por si el filtro la está ocultando
    setFoco(citaId)
    setTimeout(() => {
      cartillasRef.current[citaId]?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }, 80)
  }, [])

  const cargarTodo = useCallback(async () => {
    setErrorCarga(null)
    try {
      const [resCitas, resRefs] = await Promise.all([
        grafoAPI.verCitas(documentoId),
        grafoAPI.verReferencias(documentoId).catch(() => ({ data: { referencias: [] } })),
      ])
      setCitas(resCitas.data.citas)
      setReferencias(resRefs.data.referencias)
    } catch (e) {
      setErrorCarga(e)
    }
  }, [documentoId])

  const cargarUbicaciones = useCallback(() => {
    grafoAPI.verUbicaciones(documentoId)
      .then((res) => {
        setUbicaciones(res.data.ubicaciones)
        setPaginaReferencias(res.data.pagina_referencias)
      })
      .catch(() => setUbicaciones([])) // sin ubicaciones el visor sigue sirviendo
  }, [documentoId])

  useEffect(() => { cargarTodo() }, [cargarTodo])
  useEffect(() => { cargarUbicaciones() }, [cargarUbicaciones])

  const localizadaPorId = useMemo(() => {
    const mapa = {}
    for (const u of ubicaciones || []) mapa[u.cita_id] = u.pagina_real != null
    return mapa
  }, [ubicaciones])

  // ── Acciones sobre citas ──
  const guardarCita = async (citaId, datos) => {
    const { data } = await grafoAPI.actualizarCita(documentoId, citaId, datos)
    setCitas((prev) => prev.map((c) => (c.cita_id === citaId ? data : c)))
    cargarUbicaciones()
  }

  const eliminarCita = async (citaId) => {
    if (!window.confirm('¿Eliminar esta cita? Esta acción no se puede deshacer.')) return
    try {
      await grafoAPI.eliminarCita(documentoId, citaId)
      setCitas((prev) => prev.filter((c) => c.cita_id !== citaId))
      cargarUbicaciones()
    } catch (e) {
      setErrorAccion(e)
    }
  }

  const crearCita = async () => {
    setErrorAccion(null)
    try {
      const { data } = await grafoAPI.crearCita(documentoId, {
        texto_cita: '(Autor, año)',
        tipo: 'parentetica',
        pagina: 1,
        fragmento_oracion: '',
      })
      setCitas((prev) => [data, ...prev])
      setPestana('citas')
      setFoco(data.cita_id)
    } catch (e) {
      setErrorAccion(e)
    }
  }

  // ── Acciones sobre referencias ──
  const guardarReferencia = async (refId, datos) => {
    const { data } = await grafoAPI.actualizarReferencia(documentoId, refId, datos)
    setReferencias((prev) => prev.map((r) => (r.referencia_id === refId ? data : r)))
  }

  const eliminarReferencia = async (refId) => {
    if (!window.confirm('¿Eliminar esta referencia? Las citas vinculadas quedarán sin referencia.')) return
    try {
      await grafoAPI.eliminarReferencia(documentoId, refId)
      setReferencias((prev) => prev.filter((r) => r.referencia_id !== refId))
      cargarTodo() // refresca vínculos de citas
    } catch (e) {
      setErrorAccion(e)
    }
  }

  const crearReferencia = async () => {
    setErrorAccion(null)
    try {
      const { data } = await grafoAPI.crearReferencia(documentoId, {
        autores: [],
        titulo: 'Nueva referencia',
        datos_incompletos: true,
      })
      setReferencias((prev) => [data, ...prev])
      setPestana('referencias')
    } catch (e) {
      setErrorAccion(e)
    }
  }

  // ── Acciones globales ──
  const reVincular = async () => {
    setReVinculando(true)
    setErrorAccion(null)
    try {
      await grafoAPI.reVincular(documentoId)
      await cargarTodo()
    } catch (e) {
      setErrorAccion(e)
    } finally {
      setReVinculando(false)
    }
  }

  const confirmarRevision = async () => {
    setConfirmando(true)
    setErrorAccion(null)
    try {
      await ingestaAPI.confirmarRevision(documentoId)
      await refrescarEstado()
      navigate(`/doc/${documentoId}/verificar`)
    } catch (e) {
      setErrorAccion(e)
      setConfirmando(false)
    }
  }

  if (errorCarga) return <EstadoError error={errorCarga} onReintentar={cargarTodo} />
  if (!citas || !referencias) return <EstadoCarga mensaje="Cargando citas y referencias…" />

  const sinVincular = citas.filter((c) => !c.referencia_id).length
  const citasVisibles = soloSinVincular ? citas.filter((c) => !c.referencia_id) : citas
  const yaConfirmada = estado !== 'revision_pendiente'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <header style={{ display: 'flex', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <h2>Revisa lo que detectamos</h2>
          <p style={{ color: 'var(--text-secondary)', marginTop: 4, fontSize: 14 }}>
            Corrige las citas y referencias antes de continuar: haz clic en una cartilla
            para ver el punto exacto del PDF donde aparece.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn-contorno" onClick={reVincular} disabled={reVinculando}>
            <RefreshCcw size={14} className={reVinculando ? 'girando' : ''} />
            {reVinculando ? 'Re-vinculando…' : 'Re-vincular automáticamente'}
          </button>
          <button
            className="btn btn-primario"
            onClick={confirmarRevision}
            disabled={confirmando || yaConfirmada}
            title={yaConfirmada ? 'La revisión ya fue confirmada.' : undefined}
          >
            <CheckCheck size={15} />
            {yaConfirmada ? 'Revisión confirmada' : confirmando ? 'Confirmando…' : 'Confirmar datos y continuar'}
          </button>
        </div>
      </header>

      <ErrorInline error={errorAccion} />

      <div className="revision-grid">
        <div className="panel-pdf">
          <VisorPDF
            url={ingestaAPI.urlPDF(documentoId)}
            ubicaciones={ubicaciones}
            foco={foco}
            salto={salto}
            onSeleccionarCita={seleccionarDesdePDF}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
          <div className="pestanas" style={{ alignItems: 'center' }}>
            <button className={`pestana ${pestana === 'citas' ? 'activa' : ''}`} onClick={() => setPestana('citas')}>
              Citas ({citas.length})
            </button>
            <button className={`pestana ${pestana === 'referencias' ? 'activa' : ''}`} onClick={() => setPestana('referencias')}>
              Referencias ({referencias.length})
            </button>
            {paginaReferencias && (
              <button
                className="btn btn-contorno"
                style={{ marginLeft: 'auto', padding: '5px 12px', fontSize: 12.5 }}
                title={`Ir a la página ${paginaReferencias} del PDF`}
                onClick={() => setSalto({ pagina: paginaReferencias, t: Date.now() })}
              >
                <BookMarked size={13} />
                Ver referencias en el PDF
              </button>
            )}
          </div>

          {pestana === 'citas' && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 13 }}>
                <span style={{ color: 'var(--text-secondary)' }}>
                  {citas.length} citas · {sinVincular} sin referencia
                </span>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={soloSinVincular}
                    onChange={(e) => setSoloSinVincular(e.target.checked)}
                  />
                  solo sin vincular
                </label>
                <span style={{ flex: 1 }} />
                <button className="btn" onClick={crearCita}>
                  <Plus size={14} /> Añadir cita
                </button>
              </div>

              <div className="panel-cartillas">
                {citasVisibles.length === 0 && (
                  <EstadoVacio titulo="No hay citas que mostrar" detalle={soloSinVincular ? 'Todas las citas están vinculadas.' : undefined} />
                )}
                {citasVisibles.map((cita) => (
                  <div key={cita.cita_id} ref={(el) => { cartillasRef.current[cita.cita_id] = el }}>
                    <CartillaCita
                      cita={cita}
                      referencias={referencias}
                      localizada={localizadaPorId[cita.cita_id] !== false}
                      seleccionada={foco === cita.cita_id}
                      onEnfocar={setFoco}
                      onGuardar={guardarCita}
                      onEliminar={eliminarCita}
                    />
                  </div>
                ))}
              </div>
            </>
          )}

          {pestana === 'referencias' && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 13 }}>
                <span style={{ color: 'var(--text-secondary)' }}>
                  {referencias.length} referencias · {referencias.filter((r) => r.datos_incompletos).length} incompletas
                </span>
                <span style={{ flex: 1 }} />
                <button className="btn" onClick={crearReferencia}>
                  <Plus size={14} /> Añadir referencia
                </button>
              </div>

              <div className="panel-cartillas">
                {referencias.length === 0 && <EstadoVacio titulo="No se detectaron referencias" />}
                {referencias.map((ref) => (
                  <CartillaReferencia
                    key={ref.referencia_id}
                    referencia={ref}
                    onGuardar={guardarReferencia}
                    onEliminar={eliminarReferencia}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

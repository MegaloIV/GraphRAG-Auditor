import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { Plus, RefreshCcw, CheckCheck, BookMarked, Trash2 } from 'lucide-react'
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
  const [seleccionLote, setSeleccionLote] = useState(new Set())
  const [eliminandoLote, setEliminandoLote] = useState(false)
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

  // Workspace de altura fija mientras esta fase está activa: el body no hace
  // scroll; solo el PDF y la lista de cartillas tienen scroll interno.
  useEffect(() => {
    document.body.classList.add('workspace-fijo')
    return () => document.body.classList.remove('workspace-fijo')
  }, [])

  const localizadaPorId = useMemo(() => {
    const mapa = {}
    for (const u of ubicaciones || []) mapa[u.cita_id] = u.pagina_real != null
    return mapa
  }, [ubicaciones])

  // Orden de lectura: página real en el PDF y posición vertical dentro de la
  // página; sin ubicación, la página estimada de la extracción.
  const citasOrdenadas = useMemo(() => {
    if (!citas) return citas
    const pos = {}
    for (const u of ubicaciones || []) {
      if (u.pagina_real != null) pos[u.cita_id] = [u.pagina_real, u.rects?.[0]?.y0 ?? 0]
    }
    return [...citas].sort((a, b) => {
      const pa = pos[a.cita_id] || [a.pagina || Number.MAX_SAFE_INTEGER, 0]
      const pb = pos[b.cita_id] || [b.pagina || Number.MAX_SAFE_INTEGER, 0]
      return pa[0] - pb[0] || pa[1] - pb[1]
    })
  }, [citas, ubicaciones])

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
      setSeleccionLote((prev) => {
        const s = new Set(prev)
        s.delete(citaId)
        return s
      })
      cargarUbicaciones()
    } catch (e) {
      setErrorAccion(e)
    }
  }

  // ── Eliminación en lote ──
  const alternarSeleccionLote = useCallback((citaId) => {
    setSeleccionLote((prev) => {
      const s = new Set(prev)
      if (s.has(citaId)) s.delete(citaId)
      else s.add(citaId)
      return s
    })
  }, [])

  const eliminarSeleccionadas = async () => {
    const ids = [...seleccionLote]
    if (!ids.length) return
    if (!window.confirm(`¿Eliminar ${ids.length} cita(s) seleccionada(s)? Esta acción no se puede deshacer.`)) return
    setEliminandoLote(true)
    setErrorAccion(null)
    try {
      await grafoAPI.eliminarCitasLote(documentoId, ids)
      setCitas((prev) => prev.filter((c) => !seleccionLote.has(c.cita_id)))
      setSeleccionLote(new Set())
      if (seleccionLote.has(foco)) setFoco(null)
      cargarUbicaciones()
    } catch (e) {
      setErrorAccion(e)
    } finally {
      setEliminandoLote(false)
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
  const citasVisibles = soloSinVincular
    ? citasOrdenadas.filter((c) => !c.referencia_id)
    : citasOrdenadas
  const yaConfirmada = estado !== 'revision_pendiente'

  return (
    <div className="fase-revision">
      <header style={{ display: 'flex', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <h2>Revisa lo que detectamos</h2>
          <p className="oculta-movil" style={{ color: 'var(--text-secondary)', marginTop: 4, fontSize: 14 }}>
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

        <div className="panel-derecho">
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
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }} title="Selecciona todas las citas visibles para eliminarlas en lote">
                  <input
                    type="checkbox"
                    checked={citasVisibles.length > 0 && citasVisibles.every((c) => seleccionLote.has(c.cita_id))}
                    onChange={(e) =>
                      setSeleccionLote(e.target.checked ? new Set(citasVisibles.map((c) => c.cita_id)) : new Set())
                    }
                  />
                  seleccionar visibles
                </label>
                <span style={{ flex: 1 }} />
                {seleccionLote.size > 0 && (
                  <button className="btn btn-peligro" onClick={eliminarSeleccionadas} disabled={eliminandoLote}>
                    <Trash2 size={14} />
                    {eliminandoLote ? 'Eliminando…' : `Eliminar (${seleccionLote.size})`}
                  </button>
                )}
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
                      marcadaLote={seleccionLote.has(cita.cita_id)}
                      onMarcarLote={alternarSeleccionLote}
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

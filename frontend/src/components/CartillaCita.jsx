import { useEffect, useState } from 'react'
import { Save, Trash2, MapPinOff, Link2, Unlink } from 'lucide-react'
import SelectorReferencia from './SelectorReferencia'
import { ErrorInline } from './Estados'
import { formatearRef } from '../lib/formato'

// Cartilla editable de una cita (D4): el usuario corrige texto_cita, la
// aseveración (fragmento_oracion), la página y el vínculo con su referencia.
export default function CartillaCita({
  cita,
  referencias,
  localizada,
  seleccionada,
  marcadaLote = false,
  onMarcarLote,
  onEnfocar,
  onGuardar,
  onEliminar,
}) {
  const [form, setForm] = useState(null)
  const [guardando, setGuardando] = useState(false)
  const [guardadoOk, setGuardadoOk] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setForm({
      texto_cita: cita.texto_cita,
      fragmento_oracion: cita.fragmento_oracion || '',
      pagina: cita.pagina ?? 0,
      referencia_id: cita.referencia_id || '',
    })
    setError(null)
  }, [cita])

  if (!form) return null

  const sinGuardar =
    form.texto_cita !== cita.texto_cita ||
    form.fragmento_oracion !== (cita.fragmento_oracion || '') ||
    Number(form.pagina) !== (cita.pagina ?? 0) ||
    form.referencia_id !== (cita.referencia_id || '')

  const cambiar = (campo, valor) => {
    setForm((f) => ({ ...f, [campo]: valor }))
    setGuardadoOk(false)
  }

  const guardar = async () => {
    setGuardando(true)
    setError(null)
    try {
      await onGuardar(cita.cita_id, {
        texto_cita: form.texto_cita,
        fragmento_oracion: form.fragmento_oracion,
        pagina: Number(form.pagina) || 0,
        referencia_id: form.referencia_id,
      })
      setGuardadoOk(true)
    } catch (e) {
      setError(e)
    } finally {
      setGuardando(false)
    }
  }

  const refVinculada = referencias.find((r) => r.referencia_id === form.referencia_id)

  return (
    <div
      className={`cartilla ${seleccionada ? 'seleccionada' : ''}`}
      onClick={() => onEnfocar(cita.cita_id)}
    >
      <div className="cartilla-cabecera">
        {onMarcarLote && (
          <input
            type="checkbox"
            checked={marcadaLote}
            onClick={(e) => e.stopPropagation()}
            onChange={() => onMarcarLote(cita.cita_id)}
            title="Seleccionar para eliminar en lote"
            style={{ cursor: 'pointer' }}
          />
        )}
        <span className="badge badge-accent">
          {cita.tipo === 'parentetica' ? 'Parentética' : 'Narrativa'}
        </span>
        <span className="badge badge-neutro">pág. {form.pagina || '—'}</span>
        {refVinculada ? (
          <span className="badge badge-supports">
            <Link2 size={12} /> {formatearRef(refVinculada)}
          </span>
        ) : (
          <span className="badge badge-warn">
            <Unlink size={12} /> sin referencia
          </span>
        )}
        {!localizada && (
          <span className="badge badge-neutro" title="No se localizó en el PDF; igual puedes editarla.">
            <MapPinOff size={12} /> no localizada
          </span>
        )}
      </div>

      <div>
        <label className="campo-etiqueta">Texto de la cita</label>
        <input
          className="campo texto-doc"
          value={form.texto_cita}
          onChange={(e) => cambiar('texto_cita', e.target.value)}
        />
      </div>

      <div>
        <label className="campo-etiqueta">Aseveración del tesista</label>
        <textarea
          className="campo"
          value={form.fragmento_oracion}
          onChange={(e) => cambiar('fragmento_oracion', e.target.value)}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 110px', gap: 10 }}>
        <div>
          <label className="campo-etiqueta">Referencia vinculada</label>
          <SelectorReferencia
            referencias={referencias}
            valor={form.referencia_id}
            onSeleccionar={(refId) => cambiar('referencia_id', refId)}
          />
        </div>
        <div>
          <label className="campo-etiqueta">Página</label>
          <input
            className="campo"
            type="number"
            min="0"
            value={form.pagina}
            onChange={(e) => cambiar('pagina', e.target.value)}
          />
        </div>
      </div>

      <ErrorInline error={error} />

      <div className="cartilla-acciones">
        {sinGuardar && !guardando && (
          <span style={{ fontSize: 12, color: 'var(--warn)' }}>Cambios sin guardar</span>
        )}
        {guardadoOk && !sinGuardar && (
          <span style={{ fontSize: 12, color: 'var(--supports)' }}>Guardado ✓</span>
        )}
        <button
          className="btn btn-peligro"
          onClick={(e) => { e.stopPropagation(); onEliminar(cita.cita_id) }}
        >
          <Trash2 size={14} /> Eliminar
        </button>
        <button
          className="btn btn-primario"
          disabled={!sinGuardar || guardando}
          onClick={(e) => { e.stopPropagation(); guardar() }}
        >
          <Save size={14} /> {guardando ? 'Guardando…' : 'Guardar'}
        </button>
      </div>
    </div>
  )
}

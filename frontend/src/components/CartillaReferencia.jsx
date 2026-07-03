import { useEffect, useState } from 'react'
import { Save, Trash2, X } from 'lucide-react'
import { ErrorInline } from './Estados'

function ChipsAutores({ autores, onCambiar }) {
  const [nuevo, setNuevo] = useState('')

  const agregar = () => {
    const limpio = nuevo.trim()
    if (!limpio) return
    onCambiar([...autores, limpio])
    setNuevo('')
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
      <div className="chips">
        {autores.map((a, i) => (
          <span key={`${a}-${i}`} className="chip">
            {a}
            <button type="button" onClick={() => onCambiar(autores.filter((_, j) => j !== i))}>
              <X size={12} />
            </button>
          </span>
        ))}
      </div>
      <input
        className="campo"
        placeholder="Añadir autor (Apellido, N.) y Enter"
        value={nuevo}
        onChange={(e) => setNuevo(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); agregar() }
        }}
        onBlur={agregar}
      />
    </div>
  )
}

export default function CartillaReferencia({ referencia, onGuardar, onEliminar }) {
  const [form, setForm] = useState(null)
  const [guardando, setGuardando] = useState(false)
  const [guardadoOk, setGuardadoOk] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setForm({
      autores: referencia.autores || [],
      anio: referencia.anio ?? '',
      titulo: referencia.titulo || '',
      fuente: referencia.fuente || '',
      doi: referencia.doi || '',
      datos_incompletos: referencia.datos_incompletos || false,
    })
    setError(null)
  }, [referencia])

  if (!form) return null

  const sinGuardar =
    JSON.stringify(form.autores) !== JSON.stringify(referencia.autores || []) ||
    String(form.anio) !== String(referencia.anio ?? '') ||
    form.titulo !== (referencia.titulo || '') ||
    form.fuente !== (referencia.fuente || '') ||
    form.doi !== (referencia.doi || '') ||
    form.datos_incompletos !== (referencia.datos_incompletos || false)

  const cambiar = (campo, valor) => {
    setForm((f) => ({ ...f, [campo]: valor }))
    setGuardadoOk(false)
  }

  const guardar = async () => {
    setGuardando(true)
    setError(null)
    try {
      await onGuardar(referencia.referencia_id, {
        autores: form.autores,
        anio: form.anio === '' ? null : Number(form.anio),
        titulo: form.titulo,
        fuente: form.fuente || null,
        doi: form.doi || null,
        datos_incompletos: form.datos_incompletos,
      })
      setGuardadoOk(true)
    } catch (e) {
      setError(e)
    } finally {
      setGuardando(false)
    }
  }

  return (
    <div className="cartilla">
      <div className="cartilla-cabecera">
        {referencia.nivel_confianza && (
          <span className="badge badge-accent">{referencia.nivel_confianza}</span>
        )}
        {form.datos_incompletos && <span className="badge badge-warn">datos incompletos</span>}
      </div>

      <div>
        <label className="campo-etiqueta">Autores</label>
        <ChipsAutores autores={form.autores} onCambiar={(a) => cambiar('autores', a)} />
      </div>

      <div>
        <label className="campo-etiqueta">Título</label>
        <input
          className="campo texto-doc"
          value={form.titulo}
          onChange={(e) => cambiar('titulo', e.target.value)}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 10 }}>
        <div>
          <label className="campo-etiqueta">Año</label>
          <input
            className="campo"
            type="number"
            value={form.anio}
            onChange={(e) => cambiar('anio', e.target.value)}
          />
        </div>
        <div>
          <label className="campo-etiqueta">Fuente</label>
          <input
            className="campo"
            value={form.fuente}
            onChange={(e) => cambiar('fuente', e.target.value)}
          />
        </div>
      </div>

      <div>
        <label className="campo-etiqueta">DOI</label>
        <input
          className="campo"
          value={form.doi}
          onChange={(e) => cambiar('doi', e.target.value)}
        />
      </div>

      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13.5, cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={form.datos_incompletos}
          onChange={(e) => cambiar('datos_incompletos', e.target.checked)}
        />
        Marcar con datos incompletos
      </label>

      <ErrorInline error={error} />

      <div className="cartilla-acciones">
        {sinGuardar && !guardando && (
          <span style={{ fontSize: 12, color: 'var(--warn)' }}>Cambios sin guardar</span>
        )}
        {guardadoOk && !sinGuardar && (
          <span style={{ fontSize: 12, color: 'var(--supports)' }}>Guardado ✓</span>
        )}
        <button className="btn btn-peligro" onClick={() => onEliminar(referencia.referencia_id)}>
          <Trash2 size={14} /> Eliminar
        </button>
        <button className="btn btn-primario" disabled={!sinGuardar || guardando} onClick={guardar}>
          <Save size={14} /> {guardando ? 'Guardando…' : 'Guardar'}
        </button>
      </div>
    </div>
  )
}

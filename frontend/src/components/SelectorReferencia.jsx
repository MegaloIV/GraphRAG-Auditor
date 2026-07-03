import { useMemo, useRef, useState } from 'react'
import { coincide } from '../lib/normalizar'
import { formatearRef } from '../lib/formato'

// Selector de referencia con buscador insensible a tildes/mayúsculas (D6).
// La normalización es solo para comparar: el texto mostrado no se altera.
export default function SelectorReferencia({ referencias, valor, onSeleccionar }) {
  const [query, setQuery] = useState('')
  const [abierto, setAbierto] = useState(false)
  const blurRef = useRef(null)

  const seleccionada = referencias.find((r) => r.referencia_id === valor)

  const filtradas = useMemo(() => {
    if (!query.trim()) return referencias
    return referencias.filter(
      (r) =>
        coincide(r.titulo, query) ||
        coincide((r.autores || []).join(' '), query) ||
        coincide(String(r.anio || ''), query)
    )
  }, [referencias, query])

  const elegir = (refId) => {
    onSeleccionar(refId)
    setQuery('')
    setAbierto(false)
  }

  return (
    <div
      className="selector-ref"
      onFocus={() => { clearTimeout(blurRef.current); setAbierto(true) }}
      onBlur={() => { blurRef.current = setTimeout(() => setAbierto(false), 150) }}
    >
      <input
        className="campo"
        placeholder={seleccionada ? `${formatearRef(seleccionada)} — escribe para cambiar` : 'Buscar referencia…'}
        value={query}
        onChange={(e) => { setQuery(e.target.value); setAbierto(true) }}
      />
      {abierto && (
        <div className="selector-lista">
          <button type="button" className="selector-item" onMouseDown={() => elegir('')}>
            <em>Sin vincular</em>
          </button>
          {filtradas.map((r) => (
            <button
              type="button"
              key={r.referencia_id}
              className="selector-item"
              onMouseDown={() => elegir(r.referencia_id)}
            >
              <strong>{formatearRef(r)}</strong>
              <br />
              <span className="sec">{r.titulo}</span>
            </button>
          ))}
          {filtradas.length === 0 && (
            <div className="selector-item" style={{ cursor: 'default' }}>
              <span className="sec">Sin coincidencias.</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

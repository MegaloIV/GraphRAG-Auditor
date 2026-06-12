import { useState, useRef } from 'react'
import Badge from '../ui/Badge'
import { grafoAPI } from '../../api/client'

const ETIQUETA_CONFIANZA = {
  texto_completo: { label: 'Texto completo',    variante: 'success', icono: '✓' },
  abstract:       { label: 'Abstract',           variante: 'accent',  icono: null },
  cache:          { label: 'Caché',              variante: 'accent',  icono: null },
  manual:         { label: 'Subido manualmente', variante: 'accent',  icono: null },
  no_encontrado:  { label: 'No encontrado',      variante: 'error',   icono: '✕' },
}

export default function ListaReferencias({ documentoId, referencias, total, advertencia, onRecargarReferencias }) {
  const fileInputRef = useRef()
  const [uploadRefId, setUploadRefId] = useState(null)
  const [subiendoId, setSubiendoId]   = useState(null)
  const [errorSubida, setErrorSubida] = useState({})

  const handleSubirClick = (refId) => {
    setUploadRefId(refId)
    fileInputRef.current.value = ''
    fileInputRef.current.click()
  }

  const handleFileChange = async (e) => {
    const file = e.target.files[0]
    if (!file || !uploadRefId) return
    setSubiendoId(uploadRefId)
    setErrorSubida(prev => ({ ...prev, [uploadRefId]: null }))
    try {
      await grafoAPI.subirPaperManual(documentoId, uploadRefId, file)
      await onRecargarReferencias()
    } catch (err) {
      setErrorSubida(prev => ({ ...prev, [uploadRefId]: err.mensaje || 'Error al subir el paper.' }))
    } finally {
      setSubiendoId(null)
      setUploadRefId(null)
    }
  }

  return (
    <div>
      {/* Input oculto para subida manual */}
      <input
        ref={fileInputRef}
        type="file"
        accept="application/pdf"
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          {total} referencias detectadas
        </span>
        {advertencia && <Badge texto="Datos incompletos" variante="warning" icono="⚠️" />}
      </div>

      {advertencia && (
        <div style={{
          padding: '0.75rem 1rem',
          background: 'var(--warning-subtle)',
          border: '1px solid rgba(245, 158, 11, 0.2)',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.82rem', color: 'var(--warning)',
          marginBottom: '1rem', display: 'flex', gap: '0.5rem',
        }}>
          <span>⚠️</span>
          <span>{advertencia}</span>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', overflowY: 'auto', paddingRight: '0.25rem' }}>
        {referencias.map((ref, i) => {
          const info       = ETIQUETA_CONFIANZA[ref.nivel_confianza]
          const esSubiendo = subiendoId === ref.referencia_id
          const errSubida  = errorSubida[ref.referencia_id]
          const encontrado = ref.nivel_confianza && ref.nivel_confianza !== 'no_encontrado'

          return (
            <div
              key={ref.referencia_id}
              style={{
                padding: '0.875rem',
                background: 'var(--bg-surface-2)',
                borderRadius: 'var(--radius-md)',
                border: ref.nivel_confianza === 'no_encontrado'
                  ? '1px solid rgba(239,68,68,0.2)'
                  : ref.datos_incompletos
                  ? '1px solid rgba(245, 158, 11, 0.2)'
                  : '1px solid transparent',
                animation: `fadeIn 0.3s ease ${i * 0.03}s forwards`,
                opacity: 0,
              }}
            >
              {/* Título */}
              <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '0.3rem', lineHeight: 1.4 }}>
                {ref.titulo}
              </div>

              {/* Autores y año */}
              <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
                {ref.autores.join(', ')}
                {ref.anio && (
                  <span style={{ marginLeft: '0.5rem', fontFamily: 'var(--font-mono)', color: 'var(--accent)' }}>
                    ({ref.anio})
                  </span>
                )}
              </div>

              {/* Footer: fuente, DOI, badge, acciones */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                {ref.fuente && (
                  <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                    {ref.fuente}
                  </span>
                )}
                {ref.doi && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    DOI: {ref.doi}
                  </span>
                )}
                {info && <Badge texto={info.label} variante={info.variante} icono={info.icono} />}

                {/* Reemplazar paper (para referencias ya encontradas) */}
                {encontrado && (
                  <button
                    onClick={() => handleSubirClick(ref.referencia_id)}
                    disabled={esSubiendo}
                    style={{
                      marginLeft: 'auto', padding: '0.2rem 0.6rem',
                      background: 'transparent', border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)', fontSize: '0.68rem',
                      color: 'var(--text-muted)', cursor: esSubiendo ? 'not-allowed' : 'pointer',
                      fontFamily: 'var(--font-sans)',
                    }}
                  >
                    {esSubiendo ? 'Subiendo...' : '↺ Reemplazar paper'}
                  </button>
                )}

                {/* Adjuntar paper (para referencias no encontradas) */}
                {ref.nivel_confianza === 'no_encontrado' && (
                  <button
                    onClick={() => handleSubirClick(ref.referencia_id)}
                    disabled={esSubiendo}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
                      padding: '0.3rem 0.75rem',
                      background: 'var(--accent)', color: 'white',
                      border: 'none', borderRadius: 'var(--radius-sm)',
                      fontSize: '0.75rem', fontWeight: 600,
                      cursor: esSubiendo ? 'not-allowed' : 'pointer',
                      fontFamily: 'var(--font-sans)', opacity: esSubiendo ? 0.7 : 1,
                    }}
                  >
                    {esSubiendo ? <><Spinner color="white" size={12} /> Subiendo...</> : '📎 Adjuntar paper PDF'}
                  </button>
                )}
              </div>

              {errSubida && (
                <div style={{ marginTop: '0.35rem', fontSize: '0.72rem', color: 'var(--error)' }}>
                  ✕ {errSubida}
                </div>
              )}

              {/* Campos faltantes */}
              {ref.datos_incompletos && (() => {
                const faltantes = ref.campos_faltantes?.length > 0
                  ? ref.campos_faltantes
                  : [
                      !ref.autores?.length && 'autores',
                      !ref.anio          && 'año',
                      !ref.titulo        && 'título',
                      !ref.fuente        && 'fuente/revista',
                      !ref.doi           && 'DOI',
                    ].filter(Boolean)

                return faltantes.length > 0 ? (
                  <div style={{
                    marginTop: '0.5rem', padding: '0.5rem 0.75rem',
                    background: 'var(--warning-subtle)', border: '1px solid rgba(245,158,11,0.2)',
                    borderRadius: 'var(--radius-sm)', fontSize: '0.75rem', color: 'var(--warning)',
                    display: 'flex', alignItems: 'flex-start', gap: '0.4rem',
                  }}>
                    <span>⚠️</span>
                    <div><span style={{ fontWeight: 600 }}>Faltan: </span>{faltantes.join(', ')}</div>
                  </div>
                ) : (
                  <div style={{
                    marginTop: '0.5rem', padding: '0.5rem 0.75rem',
                    background: 'var(--warning-subtle)', border: '1px solid rgba(245,158,11,0.2)',
                    borderRadius: 'var(--radius-sm)', fontSize: '0.75rem', color: 'var(--warning)',
                    display: 'flex', gap: '0.4rem',
                  }}>
                    <span>⚠️</span>
                    <span>Referencia marcada como incompleta por el analizador.</span>
                  </div>
                )
              })()}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function Spinner({ color = 'var(--accent)', size = 16 }) {
  return (
    <div style={{
      width: size, height: size, flexShrink: 0,
      border: `2px solid ${color}30`,
      borderTop: `2px solid ${color}`,
      borderRadius: '50%',
      animation: 'spin 1s linear infinite',
    }} />
  )
}

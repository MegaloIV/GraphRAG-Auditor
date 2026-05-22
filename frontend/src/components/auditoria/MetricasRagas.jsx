import { useState } from 'react'
import { auditoriaAPI } from '../../api/client'

function descargarBlob(blob, nombre) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = nombre
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

const METRICAS = [
  { key: 'faithfulness_promedio',       label: 'Faithfulness'       },
  { key: 'answer_relevancy_promedio',   label: 'Answer Relevancy'   },
  { key: 'context_precision_promedio',  label: 'Context Precision'  },
  { key: 'context_recall_promedio',     label: 'Context Recall'     },
  { key: 'answer_correctness_promedio', label: 'Answer Correctness' },
]

function colorScore(valor) {
  if (valor == null) return 'var(--text-muted)'
  if (valor >= 0.80) return 'var(--success)'
  if (valor >= 0.60) return 'var(--warning)'
  return 'var(--error)'
}

export default function MetricasRagas({ documentoId, metricas, onMetricasActualizadas }) {
  const [evaluando, setEvaluando]     = useState(false)
  const [descargando, setDescargando] = useState(false)
  const [error, setError]             = useState(null)

  const descargarExcel = async () => {
    setDescargando(true)
    setError(null)
    try {
      const res = await auditoriaAPI.exportarMetricasExcel(documentoId)
      descargarBlob(res.data, `ragas_${documentoId}.xlsx`)
    } catch {
      setError('No se pudo descargar el Excel. Verifica que haya métricas calculadas.')
    } finally {
      setDescargando(false)
    }
  }

  const evaluar = async () => {
    setEvaluando(true)
    setError(null)
    try {
      await auditoriaAPI.evaluarRagas(documentoId)
      const res = await auditoriaAPI.verMetricas(documentoId)
      onMetricasActualizadas(res.data)
    } catch (err) {
      setError(err.mensaje || 'Error al evaluar con RAGAS.')
    } finally {
      setEvaluando(false)
    }
  }

  if (!metricas) {
    return (
      <div style={{ padding: '1rem 0' }}>
        <div style={{
          fontSize: '0.85rem',
          color: 'var(--text-secondary)',
          marginBottom: '1rem',
          lineHeight: 1.5,
        }}>
          RAGAS evalúa la calidad de la auditoría con 5 métricas semánticas
          sobre las citas que tienen fragmento de paper disponible.
        </div>

        {evaluando ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.75rem',
            padding: '1rem',
            background: 'var(--bg-surface-2)',
            borderRadius: 'var(--radius-md)',
          }}>
            <div style={{
              width: '20px',
              height: '20px',
              border: '2px solid var(--border)',
              borderTop: '2px solid var(--accent)',
              borderRadius: '50%',
              animation: 'spin 1s linear infinite',
              flexShrink: 0,
            }} />
            <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
              Evaluando citas con RAGAS... esto puede tomar varios minutos.
            </div>
          </div>
        ) : (
          <button
            onClick={evaluar}
            style={{
              padding: '0.65rem 1.25rem',
              borderRadius: 'var(--radius-md)',
              border: 'none',
              background: 'var(--accent)',
              color: 'white',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: 'pointer',
              fontFamily: 'var(--font-sans)',
            }}
          >
            Evaluar con RAGAS
          </button>
        )}

        {error && (
          <div style={{
            marginTop: '1rem',
            padding: '0.875rem',
            background: 'var(--error-subtle)',
            border: '1px solid rgba(239,68,68,0.2)',
            borderRadius: 'var(--radius-md)',
            fontSize: '0.83rem',
            color: 'var(--error)',
          }}>
            ✕ {error}
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{ animation: 'fadeIn 0.3s ease forwards' }}>
      <div style={{
        fontSize: '0.78rem',
        color: 'var(--text-muted)',
        marginBottom: '1rem',
        fontFamily: 'var(--font-mono)',
      }}>
        {metricas.total_evaluadas} cita(s) evaluada(s)
      </div>

      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
      }}>
        {METRICAS.map(({ key, label }) => {
          const valor = metricas[key]
          const color = colorScore(valor)
          const porcentaje = valor != null ? Math.max(0, Math.min(1, valor)) * 100 : 0

          return (
            <div key={key} style={{
              padding: '0.75rem 0.875rem',
              background: 'var(--bg-surface-2)',
              borderRadius: 'var(--radius-md)',
            }}>
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '0.4rem',
              }}>
                <div style={{
                  fontSize: '0.82rem',
                  color: 'var(--text-secondary)',
                  fontWeight: 500,
                }}>
                  {label}
                </div>
                <div style={{
                  fontSize: '0.85rem',
                  fontWeight: 700,
                  color: color,
                  fontFamily: 'var(--font-mono)',
                }}>
                  {valor != null ? valor.toFixed(3) : '—'}
                </div>
              </div>

              <div style={{
                width: '100%',
                height: '6px',
                background: 'var(--bg-surface)',
                borderRadius: '999px',
                overflow: 'hidden',
              }}>
                <div style={{
                  width: `${porcentaje}%`,
                  height: '100%',
                  background: color,
                  transition: 'width 0.4s ease',
                }} />
              </div>
            </div>
          )
        })}
      </div>

      <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.25rem', flexWrap: 'wrap' }}>
        <button
          onClick={evaluar}
          disabled={evaluando || descargando}
          style={{
            padding: '0.5rem 1rem',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border)',
            background: 'transparent',
            color: 'var(--text-secondary)',
            fontSize: '0.8rem',
            fontWeight: 500,
            cursor: (evaluando || descargando) ? 'not-allowed' : 'pointer',
            fontFamily: 'var(--font-sans)',
            opacity: (evaluando || descargando) ? 0.5 : 1,
          }}
        >
          {evaluando ? 'Re-evaluando...' : 'Volver a evaluar'}
        </button>

        {metricas.total_evaluadas > 0 && (
          <button
            onClick={descargarExcel}
            disabled={descargando || evaluando}
            style={{
              padding: '0.5rem 1rem',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border)',
              background: 'transparent',
              color: 'var(--text-secondary)',
              fontSize: '0.8rem',
              fontWeight: 500,
              cursor: (descargando || evaluando) ? 'not-allowed' : 'pointer',
              fontFamily: 'var(--font-sans)',
              opacity: (descargando || evaluando) ? 0.5 : 1,
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            {descargando ? 'Descargando...' : 'Descargar Excel'}
          </button>
        )}
      </div>

      {error && (
        <div style={{
          marginTop: '1rem',
          padding: '0.875rem',
          background: 'var(--error-subtle)',
          border: '1px solid rgba(239,68,68,0.2)',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.83rem',
          color: 'var(--error)',
        }}>
          ✕ {error}
        </div>
      )}
    </div>
  )
}

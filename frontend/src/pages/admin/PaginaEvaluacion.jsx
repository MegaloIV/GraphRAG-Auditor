import { useCallback, useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Calculator, Download } from 'lucide-react'
import TarjetaMetrica from '../../components/TarjetaMetrica'
import BadgeVeredicto from '../../components/BadgeVeredicto'
import { EstadoCarga, EstadoVacio, ErrorInline } from '../../components/Estados'
import { auditoriaAPI, evaluacionAPI } from '../../api/client'
import { descargarBlob, num } from '../../lib/formato'

const LABELS = ['SUPPORTS', 'REFUTES', 'NO_INFO']

// Interpretación estándar (Landis & Koch) del Kappa de Cohen.
function interpretarKappa(k) {
  if (k < 0) return 'peor que el azar'
  if (k < 0.21) return 'acuerdo leve'
  if (k < 0.41) return 'acuerdo regular'
  if (k < 0.61) return 'acuerdo moderado'
  if (k < 0.81) return 'acuerdo sustancial'
  return 'acuerdo casi perfecto'
}

function MatrizConfusion({ matriz }) {
  return (
    <div className="tabla-scroll tarjeta">
      <table className="tabla">
        <thead>
          <tr>
            <th>Experto \ Sistema</th>
            {matriz.labels.map((l) => <th key={l}>{l}</th>)}
          </tr>
        </thead>
        <tbody>
          {matriz.labels.map((label, i) => (
            <tr key={label}>
              <td><strong>{label}</strong></td>
              {matriz.filas[i].map((celda, j) => (
                <td
                  key={j}
                  style={i === j
                    ? { background: 'var(--supports-bg)', color: 'var(--supports)', fontWeight: 700 }
                    : celda > 0 ? { color: 'var(--refutes)', fontWeight: 600 } : undefined}
                >
                  {celda}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TablaCategoria({ resultado }) {
  const filas = [...resultado.por_categoria, resultado.macro, resultado.weighted]
  return (
    <div className="tabla-scroll tarjeta">
      <table className="tabla">
        <thead>
          <tr><th>Categoría</th><th>Precisión</th><th>Recall</th><th>F1</th><th>Soporte</th></tr>
        </thead>
        <tbody>
          {filas.map((c) => (
            <tr key={c.categoria} style={['macro', 'weighted'].includes(c.categoria) ? { fontWeight: 650 } : undefined}>
              <td>{c.categoria}</td>
              <td>{num(c.precision)}</td>
              <td>{num(c.recall)}</td>
              <td>{num(c.f1)}</td>
              <td>{c.soporte}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// /admin/evaluacion — desempeño del sistema frente a un experto (Kappa/F1).
// SEPARADA de RAGAS. Las etiquetas del experto se capturan aquí y viajan en
// el body de POST /evaluacion/{id}/evaluar (D3, hasta definir el loader).
export default function PaginaEvaluacion() {
  const { documentoId } = useOutletContext()
  const [veredictos, setVeredictos] = useState(null)
  const [etiquetas, setEtiquetas] = useState({})       // {cita_id: label}
  const [resultado, setResultado] = useState(null)
  const [cargando, setCargando] = useState(false)
  const [calculando, setCalculando] = useState(false)
  const [error, setError] = useState(null)

  const cargar = useCallback(async () => {
    if (!documentoId) return
    setCargando(true)
    setError(null)
    const [v, r] = await Promise.allSettled([
      auditoriaAPI.verVeredictos(documentoId),
      evaluacionAPI.verResultados(documentoId),
    ])
    setVeredictos(v.status === 'fulfilled' ? v.value.data : null)
    setResultado(r.status === 'fulfilled' ? r.value.data : null)
    setCargando(false)
  }, [documentoId])

  useEffect(() => { cargar() }, [cargar])

  const evaluar = async () => {
    setCalculando(true)
    setError(null)
    try {
      const cuerpo = Object.entries(etiquetas)
        .filter(([, label]) => label)
        .map(([cita_id, etiqueta_experto]) => ({ cita_id, etiqueta_experto }))
      const { data } = await evaluacionAPI.evaluar(documentoId, cuerpo)
      setResultado(data)
    } catch (e) {
      setError(e)
    } finally {
      setCalculando(false)
    }
  }

  const exportar = async () => {
    try {
      const { data } = await evaluacionAPI.exportarExcel(documentoId)
      descargarBlob(data, `evaluacion_${documentoId}.xlsx`)
    } catch (e) {
      setError(e)
    }
  }

  if (!documentoId) {
    return <EstadoVacio titulo="Selecciona un documento" detalle="Elige un documento en el selector de arriba." />
  }
  if (cargando) return <EstadoCarga />

  const etiquetadas = Object.values(etiquetas).filter(Boolean).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {resultado && (
        <>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn btn-contorno" onClick={exportar}>
              <Download size={14} /> Exportar Excel
            </button>
          </div>
          <div className="grid-metricas">
            <TarjetaMetrica
              etiqueta="Kappa de Cohen"
              valor={num(resultado.kappa_cohen)}
              detalle={interpretarKappa(resultado.kappa_cohen)}
              color="var(--accent)"
            />
            <TarjetaMetrica etiqueta="Evaluadas" valor={resultado.total_evaluadas} />
            <TarjetaMetrica
              etiqueta="Aciertos"
              valor={resultado.aciertos}
              detalle={`${Math.round((resultado.aciertos / resultado.total_evaluadas) * 100)}% de acuerdo`}
              color="var(--supports)"
            />
            <TarjetaMetrica etiqueta="F1 macro" valor={num(resultado.macro.f1)} />
            <TarjetaMetrica etiqueta="F1 weighted" valor={num(resultado.weighted.f1)} />
          </div>

          {(resultado.no_emparejadas_sistema > 0 || resultado.no_emparejadas_experto > 0) && (
            <div className="aviso-warn">
              No emparejadas: {resultado.no_emparejadas_sistema} citas del sistema sin etiqueta experta,{' '}
              {resultado.no_emparejadas_experto} etiquetas sin cita. Se excluyeron del cálculo.
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 14 }}>
            <div>
              <h3 style={{ fontSize: 15, marginBottom: 8 }}>Matriz de confusión</h3>
              <MatrizConfusion matriz={resultado.matriz_confusion} />
            </div>
            <div>
              <h3 style={{ fontSize: 15, marginBottom: 8 }}>Métricas por categoría</h3>
              <TablaCategoria resultado={resultado} />
            </div>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-faint)' }}>
            Evaluado el {new Date(resultado.evaluado_en).toLocaleString()}.
          </p>
        </>
      )}

      <section style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <header style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 240 }}>
            <h3 style={{ fontSize: 15 }}>Etiquetas del experto</h3>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Asigna el veredicto correcto a cada cita y calcula las métricas.
            </p>
          </div>
          <button className="btn btn-primario" onClick={evaluar} disabled={etiquetadas === 0 || calculando}>
            <Calculator size={14} />
            {calculando ? 'Calculando…' : `Calcular métricas (${etiquetadas})`}
          </button>
        </header>

        <ErrorInline error={error} />

        {!veredictos ? (
          <EstadoVacio
            titulo="El documento aún no fue auditado"
            detalle="La evaluación compara los veredictos del sistema con el experto: primero ejecuta la auditoría."
          />
        ) : (
          <div className="tabla-scroll tarjeta">
            <table className="tabla">
              <thead>
                <tr><th>Cita</th><th>Sistema</th><th>Etiqueta del experto</th></tr>
              </thead>
              <tbody>
                {veredictos.veredictos.map((v) => (
                  <tr key={v.cita_id}>
                    <td className="texto-doc">{v.texto_cita}</td>
                    <td><BadgeVeredicto veredicto={v.veredicto} soloCodigo /></td>
                    <td>
                      <select
                        className="campo"
                        style={{ maxWidth: 180 }}
                        value={etiquetas[v.cita_id] || ''}
                        onChange={(e) => setEtiquetas((prev) => ({ ...prev, [v.cita_id]: e.target.value }))}
                      >
                        <option value="">— sin etiquetar —</option>
                        {LABELS.map((l) => <option key={l} value={l}>{l}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

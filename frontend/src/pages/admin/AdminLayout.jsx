import { NavLink, Outlet, useSearchParams } from 'react-router-dom'
import TopBar from '../../components/TopBar'
import { listarRecientes } from '../../store/documentoStore'

// Apartado de administración (D7/D8): ruta separada, sin login, NO enlazada
// desde el flujo del usuario. RAGAS y Evaluación viven como vistas separadas.
export default function AdminLayout() {
  const [params, setParams] = useSearchParams()
  const recientes = listarRecientes()
  const documentoId = params.get('doc') || ''

  return (
    <div>
      <TopBar />
      <main style={{ maxWidth: 1080, margin: '0 auto', padding: '26px 20px', display: 'flex', flexDirection: 'column', gap: 18 }}>
        <header style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 240 }}>
            <h1 style={{ fontSize: 22 }}>Administración</h1>
            <p style={{ color: 'var(--text-secondary)', fontSize: 13.5 }}>
              Métricas internas del sistema — no forma parte del flujo del usuario.
            </p>
          </div>
          <div style={{ minWidth: 280 }}>
            <label className="campo-etiqueta">Documento</label>
            <select
              className="campo"
              value={documentoId}
              onChange={(e) => setParams(e.target.value ? { doc: e.target.value } : {})}
            >
              <option value="">— Selecciona o pega un ID abajo —</option>
              {recientes.map((d) => (
                <option key={d.documentoId} value={d.documentoId}>
                  {d.nombreArchivo} ({d.documentoId.slice(0, 8)}…)
                </option>
              ))}
            </select>
            <input
              className="campo"
              style={{ marginTop: 6 }}
              placeholder="…o pega un documento_id"
              defaultValue={documentoId}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && e.target.value.trim()) {
                  setParams({ doc: e.target.value.trim() })
                }
              }}
            />
          </div>
        </header>

        <nav className="pestanas">
          <NavLink
            to={`/admin/ragas${documentoId ? `?doc=${documentoId}` : ''}`}
            className={({ isActive }) => `pestana ${isActive ? 'activa' : ''}`}
          >
            RAGAS (calidad interna)
          </NavLink>
          <NavLink
            to={`/admin/evaluacion${documentoId ? `?doc=${documentoId}` : ''}`}
            className={({ isActive }) => `pestana ${isActive ? 'activa' : ''}`}
          >
            Evaluación vs experto (Kappa/F1)
          </NavLink>
        </nav>

        <Outlet context={{ documentoId }} />
      </main>
    </div>
  )
}

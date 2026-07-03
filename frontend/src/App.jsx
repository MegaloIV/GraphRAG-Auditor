import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './store/temaStore'
import './store/accentStore'
import PaginaInicio from './pages/PaginaInicio'
import WorkspaceDocumento from './pages/WorkspaceDocumento'
import PaginaProgreso from './pages/PaginaProgreso'
import PaginaRevision from './pages/PaginaRevision'
import PaginaVerificar from './pages/PaginaVerificar'
import PaginaGrafo from './pages/PaginaGrafo'
import PaginaAuditoria from './pages/PaginaAuditoria'
import PaginaCierre from './pages/PaginaCierre'
import AdminLayout from './pages/admin/AdminLayout'
import PaginaRagas from './pages/admin/PaginaRagas'
import PaginaEvaluacion from './pages/admin/PaginaEvaluacion'
import './index.css'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<PaginaInicio />} />

        {/* Workspace de documento único con riel de fases (D11) */}
        <Route path="/doc/:documentoId" element={<WorkspaceDocumento />}>
          <Route index element={<Navigate to="progreso" replace />} />
          <Route path="progreso" element={<PaginaProgreso />} />
          <Route path="revision" element={<PaginaRevision />} />
          <Route path="verificar" element={<PaginaVerificar />} />
          <Route path="grafo" element={<PaginaGrafo />} />
          <Route path="auditoria" element={<PaginaAuditoria />} />
          <Route path="cierre" element={<PaginaCierre />} />
        </Route>

        {/* Administración (D7/D8): sin login, no enlazada desde el flujo */}
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="ragas" replace />} />
          <Route path="ragas" element={<PaginaRagas />} />
          <Route path="evaluacion" element={<PaginaEvaluacion />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

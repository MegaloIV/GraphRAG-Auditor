import { useState, useEffect } from 'react'
import { cargarAccent } from './store/accentStore'
import PaginaInicio from './pages/PaginaInicio'
import PaginaAuditoria from './pages/PaginaAuditoria'
import './index.css'

export default function App() {
  const [pagina, setPagina] = useState('inicio')
  const [documentoId, setDocumentoId] = useState(null)

  useEffect(() => {
    cargarAccent()
  }, [])

  const irAuditoria = (id) => {
    setDocumentoId(id)
    setPagina('auditoria')
  }

  const irInicio = () => {
    setDocumentoId(null)
    setPagina('inicio')
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-base)' }}>
      {pagina === 'inicio' && (
        <PaginaInicio onDocumentoCargado={irAuditoria} />
      )}
      {pagina === 'auditoria' && (
        <PaginaAuditoria
          documentoId={documentoId}
          onVolver={irInicio}
        />
      )}
    </div>
  )
}
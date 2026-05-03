import { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import Navbar from '../components/ui/Navbar'
import Card from '../components/ui/Card'
import { ingestaAPI } from '../api/client'

export default function PaginaInicio({ onDocumentoCargado }) {
  const [estado, setEstado] = useState('idle')
  const [error, setError] = useState(null)

  const onDrop = useCallback(async (archivosAceptados) => {
    const archivo = archivosAceptados[0]
    if (!archivo) return

    setEstado('cargando')
    setError(null)

    try {
      const response = await ingestaAPI.cargarPDF(archivo)
      onDocumentoCargado(response.data.documento_id)
    } catch (err) {
      setEstado('error')
      setError(err)
    }
  }, [onDocumentoCargado])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'application/pdf': ['.pdf'] },
    maxFiles: 1,
    disabled: estado === 'cargando',
  })

  const borderColor = isDragActive
    ? 'var(--accent)'
    : estado === 'error'
    ? 'var(--error)'
    : 'var(--border)'

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-base)' }}>
      <Navbar />

      <div style={{
        maxWidth: '680px',
        margin: '0 auto',
        padding: '3rem 1.5rem',
      }}>

        {/* Hero */}
        <div style={{
          textAlign: 'center',
          marginBottom: '2.5rem',
          animation: 'fadeIn 0.4s ease forwards',
        }}>
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.5rem',
            padding: '0.4rem 1rem',
            background: 'var(--accent-subtle)',
            border: '1px solid rgba(59,130,246,0.2)',
            borderRadius: '999px',
            fontSize: '0.78rem',
            color: 'var(--accent)',
            fontWeight: 600,
            marginBottom: '1.25rem',
            letterSpacing: '0.03em',
          }}>
            ⬡ GraphRAG Auditor · APA 7ma edición
          </div>

          <h1 style={{
            fontSize: '2.2rem',
            fontWeight: 700,
            color: 'var(--text-primary)',
            lineHeight: 1.2,
            marginBottom: '0.75rem',
          }}>
            Auditoría semántica
            <br />
            <span style={{ color: 'var(--accent)' }}>de referencias</span>
          </h1>

          <p style={{
            fontSize: '1rem',
            color: 'var(--text-secondary)',
            maxWidth: '480px',
            margin: '0 auto',
            lineHeight: 1.6,
          }}>
            Sube tu tesis y el sistema verificará automáticamente
            que cada cita sea fiel al documento original.
          </p>
        </div>

        {/* Zona de carga */}
        <Card>
          <div
            {...getRootProps()}
            style={{
              border: `2px dashed ${borderColor}`,
              borderRadius: 'var(--radius-lg)',
              background: isDragActive ? 'var(--accent-subtle)' : 'var(--bg-surface-2)',
              padding: '3rem 2rem',
              textAlign: 'center',
              cursor: estado === 'cargando' ? 'not-allowed' : 'pointer',
              transition: 'all 0.2s ease',
              outline: 'none',
            }}
          >
            <input {...getInputProps()} />

            <div style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>
              {estado === 'cargando' ? '⏳' : isDragActive ? '📂' : '📄'}
            </div>

            <div style={{
              fontSize: '1rem',
              fontWeight: 600,
              color: 'var(--text-primary)',
              marginBottom: '0.5rem',
            }}>
              {estado === 'cargando'
                ? 'Procesando y analizando documento...'
                : isDragActive
                ? 'Suelta el archivo aquí'
                : 'Arrastra tu tesis aquí'}
            </div>

            {estado !== 'cargando' && (
              <div style={{
                fontSize: '0.85rem',
                color: 'var(--text-muted)',
                marginBottom: '1.5rem',
              }}>
                o haz click para seleccionar un archivo
              </div>
            )}

            {estado === 'cargando' && (
              <div style={{
                width: '32px',
                height: '32px',
                border: '3px solid var(--border)',
                borderTop: '3px solid var(--accent)',
                borderRadius: '50%',
                margin: '1rem auto',
                animation: 'spin 1s linear infinite',
              }} />
            )}

            {estado !== 'cargando' && (
              <div style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.4rem 1rem',
                background: 'var(--bg-surface)',
                borderRadius: '999px',
                fontSize: '0.75rem',
                color: 'var(--text-muted)',
              }}>
                <span>PDF</span>
                <span style={{ color: 'var(--border)' }}>·</span>
                <span>Máximo 10 MB</span>
                <span style={{ color: 'var(--border)' }}>·</span>
                <span>APA 7ma edición</span>
              </div>
            )}
          </div>

          {/* Error */}
          {estado === 'error' && error && (
            <div style={{
              marginTop: '1rem',
              padding: '1rem',
              background: 'var(--error-subtle)',
              border: '1px solid rgba(239, 68, 68, 0.2)',
              borderRadius: 'var(--radius-md)',
              animation: 'fadeIn 0.3s ease forwards',
            }}>
              <div style={{
                fontSize: '0.85rem',
                fontWeight: 600,
                color: 'var(--error)',
                marginBottom: '0.25rem',
              }}>
                {error.mensaje || 'Error al cargar el archivo'}
              </div>
              {error.accion && (
                <div style={{
                  fontSize: '0.8rem',
                  color: 'var(--text-secondary)',
                }}>
                  {error.accion}
                </div>
              )}
              <button
                onClick={() => { setEstado('idle'); setError(null) }}
                style={{
                  marginTop: '0.75rem',
                  padding: '0.4rem 1rem',
                  background: 'var(--error)',
                  color: 'white',
                  border: 'none',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '0.8rem',
                  cursor: 'pointer',
                  fontFamily: 'var(--font-sans)',
                }}
              >
                Intentar de nuevo
              </button>
            </div>
          )}
        </Card>

        {/* Info */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: '0.75rem',
          marginTop: '1.5rem',
          animation: 'fadeIn 0.5s ease 0.2s forwards',
          opacity: 0,
        }}>
          {[
            { icono: '🔗', label: 'CrossRef', desc: 'Verifica existencia' },
            { icono: '🧠', label: 'GraphRAG', desc: 'Análisis semántico' },
            { icono: '⬡', label: 'Neo4j', desc: 'Grafo de conocimiento' },
          ].map((item, i) => (
            <div key={i} style={{
              padding: '1rem',
              background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              textAlign: 'center',
            }}>
              <div style={{ fontSize: '1.5rem', marginBottom: '0.4rem' }}>
                {item.icono}
              </div>
              <div style={{
                fontSize: '0.8rem',
                fontWeight: 600,
                color: 'var(--text-primary)',
              }}>
                {item.label}
              </div>
              <div style={{
                fontSize: '0.72rem',
                color: 'var(--text-muted)',
                marginTop: '2px',
              }}>
                {item.desc}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
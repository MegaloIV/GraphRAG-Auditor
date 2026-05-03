import { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { ingestaAPI } from '../../api/client'

export default function ZonaCarga({ onCargaExitosa }) {
  const [estado, setEstado] = useState('idle') // idle | cargando | error
  const [error, setError] = useState(null)

  const onDrop = useCallback(async (archivosAceptados) => {
    const archivo = archivosAceptados[0]
    if (!archivo) return

    setEstado('cargando')
    setError(null)

    try {
      const response = await ingestaAPI.cargarPDF(archivo)
      const datos = response.data
      setEstado('idle')
      onCargaExitosa(datos)
    } catch (err) {
      setEstado('error')
      setError(err)
    }
  }, [onCargaExitosa])

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

  const bgColor = isDragActive
    ? 'var(--accent-subtle)'
    : 'var(--bg-surface)'

  return (
    <div>
      {/* Zona de drop */}
      <div
        {...getRootProps()}
        style={{
          border: `2px dashed ${borderColor}`,
          borderRadius: 'var(--radius-lg)',
          background: bgColor,
          padding: '3rem 2rem',
          textAlign: 'center',
          cursor: estado === 'cargando' ? 'not-allowed' : 'pointer',
          transition: 'all 0.2s ease',
          outline: 'none',
        }}
      >
        <input {...getInputProps()} />

        {/* Icono */}
        <div style={{
          fontSize: '2.5rem',
          marginBottom: '1rem',
          opacity: estado === 'cargando' ? 0.5 : 1,
        }}>
          {estado === 'cargando' ? '⏳' : isDragActive ? '📂' : '📄'}
        </div>

        {/* Texto principal */}
        <div style={{
          fontSize: '1rem',
          fontWeight: 600,
          color: 'var(--text-primary)',
          marginBottom: '0.5rem',
        }}>
          {estado === 'cargando'
            ? 'Procesando documento...'
            : isDragActive
            ? 'Suelta el archivo aquí'
            : 'Arrastra tu tesis aquí'}
        </div>

        {/* Texto secundario */}
        {estado !== 'cargando' && (
          <div style={{
            fontSize: '0.85rem',
            color: 'var(--text-muted)',
            marginBottom: '1.5rem',
          }}>
            o haz click para seleccionar un archivo
          </div>
        )}

        {/* Spinner */}
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

        {/* Restricciones */}
        {estado !== 'cargando' && (
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.5rem',
            padding: '0.4rem 1rem',
            background: 'var(--bg-surface-2)',
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
    </div>
  )
}
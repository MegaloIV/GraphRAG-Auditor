import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useDropzone } from 'react-dropzone'
import { FileUp, FileText, ArrowRight } from 'lucide-react'
import TopBar from '../components/TopBar'
import { ErrorInline } from '../components/Estados'
import { ingestaAPI } from '../api/client'
import { listarRecientes, registrarDocumento } from '../store/documentoStore'

const MAX_PDF_MB = 10 // debe coincidir con MAX_PDF_SIZE_MB del backend

export default function PaginaInicio() {
  const navigate = useNavigate()
  const [subiendo, setSubiendo] = useState(false)
  const [error, setError] = useState(null)
  const recientes = listarRecientes()

  const subir = async (archivos) => {
    const archivo = archivos[0]
    if (!archivo) return
    if (archivo.size > MAX_PDF_MB * 1024 * 1024) {
      setError({
        mensaje: `El archivo supera el límite de ${MAX_PDF_MB} MB.`,
        accion: 'Comprime el PDF o divide el documento.',
      })
      return
    }
    setSubiendo(true)
    setError(null)
    try {
      const { data } = await ingestaAPI.cargarPDF(archivo)
      registrarDocumento({
        documentoId: data.documento_id,
        nombreArchivo: data.nombre_archivo,
        paginas: data.paginas,
      })
      navigate(`/doc/${data.documento_id}/progreso`)
    } catch (e) {
      setError(e)
      setSubiendo(false)
    }
  }

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: subir,
    accept: { 'application/pdf': ['.pdf'] },
    maxFiles: 1,
    disabled: subiendo,
  })

  return (
    <div>
      <TopBar />
      <main style={{ maxWidth: 760, margin: '0 auto', padding: '48px 20px', display: 'flex', flexDirection: 'column', gap: 28 }}>
        <header style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <h1 style={{ fontSize: 30 }}>Audita las citas de tu tesis</h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: 16 }}>
            Sube el PDF y GraphAuditor verificará que cada cita sea fiel a su fuente,
            paso a paso y con tu revisión en el medio.
          </p>
        </header>

        <div {...getRootProps()} className={`dropzone ${isDragActive ? 'activa' : ''}`}>
          <input {...getInputProps()} />
          {subiendo ? (
            <div className="estado-centro" style={{ padding: 0 }}>
              <div className="spinner" />
              <p>Subiendo documento…</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
              <FileUp size={38} color="var(--accent)" strokeWidth={1.5} />
              <strong style={{ fontSize: 16 }}>
                {isDragActive ? 'Suelta el PDF aquí' : 'Arrastra tu tesis en PDF o haz clic para elegirla'}
              </strong>
              <span style={{ fontSize: 13, color: 'var(--text-faint)' }}>
                Un documento a la vez · máximo {MAX_PDF_MB} MB
              </span>
            </div>
          )}
        </div>

        <ErrorInline error={error} />

        {recientes.length > 0 && (
          <section style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <h2 style={{ fontSize: 16, color: 'var(--text-secondary)' }}>Documentos recientes</h2>
            {recientes.map((d) => (
              <button
                key={d.documentoId}
                className="tarjeta tarjeta-pad"
                style={{ display: 'flex', alignItems: 'center', gap: 14, cursor: 'pointer', textAlign: 'left', width: '100%' }}
                onClick={() => navigate(`/doc/${d.documentoId}/progreso`)}
              >
                <FileText size={20} color="var(--accent)" />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <strong style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {d.nombreArchivo}
                  </strong>
                  <span style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>
                    {d.paginas ? `${d.paginas} páginas · ` : ''}
                    {new Date(d.cargadoEn).toLocaleDateString()}
                  </span>
                </span>
                <ArrowRight size={17} color="var(--text-faint)" />
              </button>
            ))}
          </section>
        )}
      </main>
    </div>
  )
}

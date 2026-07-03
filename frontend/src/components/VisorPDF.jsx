import { useEffect, useMemo, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/TextLayer.css'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import { EstadoCarga, EstadoError } from './Estados'

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url
).toString()

const ANCHO_PAGINA = 700

// Visor del PDF real (B1): renderiza todas las páginas y dibuja overlays de
// resaltado con los rects que calcula el backend (escalados por las
// dimensiones reales de página). Al cambiar `foco`, salta a la página y
// parpadea el resaltado ~2 s.
export default function VisorPDF({ url, ubicaciones, foco }) {
  const [numPaginas, setNumPaginas] = useState(null)
  const [error, setError] = useState(null)
  const [parpadeo, setParpadeo] = useState(false)
  const contRef = useRef(null)
  const paginasRef = useRef({})

  // rects por página de la cita enfocada
  const rectsPorPagina = useMemo(() => {
    const mapa = {}
    if (!foco || !ubicaciones) return mapa
    const ubicacion = ubicaciones.find((u) => u.cita_id === foco)
    for (const r of ubicacion?.rects || []) {
      if (!mapa[r.pagina]) mapa[r.pagina] = []
      mapa[r.pagina].push(r)
    }
    return mapa
  }, [foco, ubicaciones])

  useEffect(() => {
    if (!foco || !ubicaciones) return
    const ubicacion = ubicaciones.find((u) => u.cita_id === foco)
    if (!ubicacion?.pagina_real) return
    const nodo = paginasRef.current[ubicacion.pagina_real]
    if (nodo) nodo.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setParpadeo(true)
    const t = setTimeout(() => setParpadeo(false), 2000)
    return () => clearTimeout(t)
  }, [foco, ubicaciones])

  if (error) {
    return <EstadoError error={{ mensaje: 'No se pudo cargar el PDF.', accion: 'Vuelve a cargar el documento.' }} />
  }

  return (
    <div ref={contRef}>
      <Document
        file={url}
        onLoadSuccess={({ numPages }) => setNumPaginas(numPages)}
        onLoadError={() => setError(true)}
        loading={<EstadoCarga mensaje="Cargando PDF…" />}
      >
        {numPaginas &&
          Array.from({ length: numPaginas }, (_, i) => i + 1).map((num) => (
            <div
              key={num}
              className="pagina-pdf"
              ref={(el) => { paginasRef.current[num] = el }}
            >
              <Page
                pageNumber={num}
                width={ANCHO_PAGINA}
                renderTextLayer={false}
                renderAnnotationLayer={false}
              />
              {(rectsPorPagina[num] || []).map((r, i) => {
                const escala = ANCHO_PAGINA / r.ancho_pagina
                return (
                  <div
                    key={i}
                    className={`overlay-resaltado ${parpadeo ? 'parpadeo' : ''}`}
                    style={{
                      left: r.x0 * escala,
                      top: r.y0 * escala,
                      width: (r.x1 - r.x0) * escala,
                      height: (r.y1 - r.y0) * escala,
                    }}
                  />
                )
              })}
              <span
                style={{
                  position: 'absolute', bottom: 4, right: 8, fontSize: 11,
                  color: 'var(--text-faint)', background: 'var(--bg-surface)',
                  padding: '1px 6px', borderRadius: 4, opacity: 0.85,
                }}
              >
                {num}
              </span>
            </div>
          ))}
      </Document>
    </div>
  )
}

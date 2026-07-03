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
const VENTANA = 4 // páginas renderizadas a cada lado de la visible

// Visor del PDF real (B1) con capa de texto seleccionable (copiar/pegar hacia
// las cartillas) y renderizado perezoso: solo se montan las páginas cercanas
// a la visible; el resto son placeholders de altura estimada.
// - `foco`: cita enfocada — salta a su página y parpadea el resaltado ~2 s.
// - `salto` ({ pagina, t }): salto directo a una página (botón de referencias).
// - `onSeleccionarCita(citaId)`: toda cita localizada es un hotspot clicable
//   en el PDF; al hacer clic se selecciona su cartilla (sin mover el PDF).
export default function VisorPDF({ url, ubicaciones, foco, salto, onSeleccionarCita }) {
  const [numPaginas, setNumPaginas] = useState(null)
  const [error, setError] = useState(null)
  const [parpadeo, setParpadeo] = useState(false)
  const [paginaVisible, setPaginaVisible] = useState(1)
  const [altoPagina, setAltoPagina] = useState(Math.round(ANCHO_PAGINA * 1.414))
  const paginasRef = useRef({})
  const observerRef = useRef(null)
  // El clic vino del propio PDF: mostrar el resaltado pero no hacer scroll.
  const omitirScrollRef = useRef(false)

  // Hotspots clicables de TODAS las citas localizadas, por página.
  const hotspotsPorPagina = useMemo(() => {
    const mapa = {}
    for (const u of ubicaciones || []) {
      for (const r of u.rects || []) {
        if (!mapa[r.pagina]) mapa[r.pagina] = []
        mapa[r.pagina].push({ citaId: u.cita_id, texto: u.texto_cita, rect: r })
      }
    }
    return mapa
  }, [ubicaciones])

  // Rects del resaltado visible (solo la cita enfocada).
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

  // Observa qué página está en pantalla para mover la ventana de renderizado.
  useEffect(() => {
    if (!numPaginas) return
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries.find((e) => e.isIntersecting)
        if (visible) setPaginaVisible(Number(visible.target.dataset.pagina))
      },
      { threshold: 0.05 }
    )
    observerRef.current = obs
    Object.values(paginasRef.current).forEach((el) => el && obs.observe(el))
    return () => obs.disconnect()
  }, [numPaginas])

  // Salto a la página de la cita enfocada: renderizarla primero (ventana) y
  // luego hacer scroll; el resaltado parpadea ~2 s. Si la selección vino de
  // un clic dentro del PDF, se omite el scroll (la cita ya está a la vista).
  useEffect(() => {
    if (!foco || !ubicaciones) return
    const ubicacion = ubicaciones.find((u) => u.cita_id === foco)
    if (!ubicacion?.pagina_real) return
    let t0 = null
    if (omitirScrollRef.current) {
      omitirScrollRef.current = false
    } else {
      setPaginaVisible(ubicacion.pagina_real)
      t0 = setTimeout(() => {
        paginasRef.current[ubicacion.pagina_real]?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        })
      }, 60)
    }
    setParpadeo(true)
    const t1 = setTimeout(() => setParpadeo(false), 2000)
    return () => { if (t0) clearTimeout(t0); clearTimeout(t1) }
  }, [foco, ubicaciones])

  // Salto directo a una página (sin resaltado).
  useEffect(() => {
    if (!salto?.pagina) return
    setPaginaVisible(salto.pagina)
    const t = setTimeout(() => {
      paginasRef.current[salto.pagina]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 60)
    return () => clearTimeout(t)
  }, [salto])

  if (error) {
    return <EstadoError error={{ mensaje: 'No se pudo cargar el PDF.', accion: 'Vuelve a cargar el documento.' }} />
  }

  return (
    <Document
      file={url}
      onLoadSuccess={({ numPages }) => setNumPaginas(numPages)}
      onLoadError={() => setError(true)}
      loading={<EstadoCarga mensaje="Cargando PDF…" />}
    >
      {numPaginas &&
        Array.from({ length: numPaginas }, (_, i) => i + 1).map((num) => {
          const renderizar = Math.abs(num - paginaVisible) <= VENTANA
          const escalar = (r) => {
            const escala = ANCHO_PAGINA / r.ancho_pagina
            return {
              left: r.x0 * escala,
              top: r.y0 * escala,
              width: (r.x1 - r.x0) * escala,
              height: (r.y1 - r.y0) * escala,
            }
          }
          return (
            <div
              key={num}
              className="pagina-pdf"
              data-pagina={num}
              style={renderizar ? undefined : { width: ANCHO_PAGINA, height: altoPagina, background: 'var(--bg-surface)' }}
              ref={(el) => { paginasRef.current[num] = el }}
            >
              {renderizar && (
                <>
                  <Page
                    pageNumber={num}
                    width={ANCHO_PAGINA}
                    renderAnnotationLayer={false}
                    onLoadSuccess={(page) => {
                      const alto = Math.round((page.height / page.width) * ANCHO_PAGINA)
                      setAltoPagina((prev) => (prev === alto ? prev : alto))
                    }}
                  />
                  {(hotspotsPorPagina[num] || []).map((h, i) => (
                    <div
                      key={`h${i}`}
                      className={`hotspot-cita ${h.citaId === foco ? 'activa' : ''}`}
                      style={escalar(h.rect)}
                      title={`Editar la cartilla de ${h.texto}`}
                      onClick={() => {
                        if (!onSeleccionarCita) return
                        omitirScrollRef.current = true
                        onSeleccionarCita(h.citaId)
                      }}
                    />
                  ))}
                  {(rectsPorPagina[num] || []).map((r, i) => (
                    <div
                      key={i}
                      className={`overlay-resaltado ${parpadeo ? 'parpadeo' : ''}`}
                      style={escalar(r)}
                    />
                  ))}
                </>
              )}
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
          )
        })}
    </Document>
  )
}

import { useEffect, useRef, useState } from 'react'
import { ingestaAPI } from '../api/client'

const ESTADOS_TERMINALES = new Set([
  'completado',
  'error',
  'listo_extraccion',
  'revision_pendiente',
])

// Progreso en tiempo real vía SSE, con reintento de conexión y fallback a
// polling del mismo endpoint para que la barra no se congele si cae la red.
export function useSSEProgreso(documentoId) {
  const [progreso, setProgreso] = useState(null)
  const [conectado, setConectado] = useState(false)
  const refs = useRef({ es: null, poll: null, reintento: null, vivo: true })

  useEffect(() => {
    if (!documentoId) return
    const r = refs.current
    r.vivo = true

    const esTerminal = (p) => p && ESTADOS_TERMINALES.has(p.estado)

    const detenerTodo = () => {
      if (r.es) { r.es.close(); r.es = null }
      if (r.poll) { clearInterval(r.poll); r.poll = null }
      if (r.reintento) { clearTimeout(r.reintento); r.reintento = null }
    }

    const recibir = (p) => {
      if (!r.vivo) return
      setProgreso(p)
      if (esTerminal(p)) detenerTodo()
    }

    const iniciarPolling = () => {
      if (r.poll || !r.vivo) return
      r.poll = setInterval(async () => {
        try {
          recibir(await ingestaAPI.obtenerEstado(documentoId))
        } catch { /* siguiente tick */ }
      }, 3000)
    }

    const conectar = () => {
      if (!r.vivo) return
      const es = new EventSource(ingestaAPI.urlProgresoSSE(documentoId))
      r.es = es
      es.onopen = () => {
        setConectado(true)
        if (r.poll) { clearInterval(r.poll); r.poll = null }
      }
      es.onmessage = (ev) => {
        try { recibir(JSON.parse(ev.data)) } catch { /* keep-alive */ }
      }
      es.onerror = () => {
        es.close()
        r.es = null
        setConectado(false)
        // El backend cierra el stream al llegar a un estado terminal: en ese
        // caso no reconectamos. Si no era terminal, reintentamos + polling.
        setProgreso((actual) => {
          if (!esTerminal(actual) && r.vivo) {
            iniciarPolling()
            r.reintento = setTimeout(conectar, 4000)
          }
          return actual
        })
      }
    }

    conectar()
    return () => {
      r.vivo = false
      detenerTodo()
    }
  }, [documentoId])

  return { progreso, conectado }
}

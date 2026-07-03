import axios from 'axios'

export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

const client = axios.create({
  baseURL: API_BASE,
  timeout: 300000,
})

// Traduce el ErrorResponse del backend ({codigo, mensaje, accion_sugerida})
// a un shape uniforme que las pantallas muestran tal cual.
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = error.response?.data?.detail
    if (detail && typeof detail === 'object') {
      return Promise.reject({
        codigo: detail.codigo,
        mensaje: detail.mensaje,
        accion: detail.accion_sugerida,
      })
    }
    return Promise.reject({
      codigo: 'ERROR_RED',
      mensaje: 'No se pudo conectar con el servidor.',
      accion: 'Verifica que el backend esté corriendo.',
    })
  }
)

// ── Salud ────────────────────────────────────────────────
export const saludAPI = {
  // /health cuelga de la raíz del server, no de /api/v1
  verificar: () =>
    axios.get(API_BASE.replace(/\/api\/v1\/?$/, '') + '/health', { timeout: 5000 }),
}

// ── Ingesta ──────────────────────────────────────────────
export const ingestaAPI = {
  cargarPDF: (archivo) => {
    const form = new FormData()
    form.append('archivo', archivo)
    return client.post('/ingesta/cargar', form)
  },
  urlProgresoSSE: (documentoId) => `${API_BASE}/ingesta/${documentoId}/progreso`,
  urlPDF: (documentoId) => `${API_BASE}/ingesta/${documentoId}/pdf`,
  iniciarVerificacion: (documentoId, referenciaIds) =>
    client.post(`/ingesta/${documentoId}/verificar`, { referencia_ids: referenciaIds }),
  importarZotero: (documentoId, archivo) => {
    const form = new FormData()
    form.append('archivo', archivo)
    return client.post(`/ingesta/${documentoId}/importar-zotero`, form, { timeout: 120000 })
  },
  resultadoZotero: (documentoId) =>
    client.get(`/ingesta/${documentoId}/importar-zotero/resultado`),
  confirmarRevision: (documentoId) =>
    client.post(`/ingesta/${documentoId}/confirmar-revision`),
  // El endpoint SSE emite el estado actual y cierra si es terminal:
  // sirve también como "GET estado" para el gating del riel de fases.
  obtenerEstado: async (documentoId) => {
    const res = await fetch(`${API_BASE}/ingesta/${documentoId}/progreso`)
    if (!res.ok) throw new Error('estado no disponible')
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let crudo = ''
    let ultimo = null
    // Leemos hasta que aparezca el primer evento con datos; si el estado no es
    // terminal el stream sigue abierto, así que cortamos en cuanto hay uno.
    while (true) {
      const { value, done } = await reader.read()
      if (value) crudo += decoder.decode(value, { stream: true })
      for (const linea of crudo.split('\n')) {
        if (linea.startsWith('data: ')) {
          try { ultimo = JSON.parse(linea.slice(6)) } catch { /* parcial */ }
        }
      }
      if (ultimo || done) break
    }
    reader.cancel().catch(() => {})
    if (!ultimo) throw new Error('estado no disponible')
    return ultimo
  },
}

// ── Grafo ────────────────────────────────────────────────
export const grafoAPI = {
  verReferencias: (documentoId) => client.get(`/grafo/${documentoId}/referencias`),
  verCitas: (documentoId) => client.get(`/grafo/${documentoId}/citas`),
  verResumen: (documentoId) => client.get(`/grafo/${documentoId}/resumen`),
  verGrafoVisual: (documentoId) => client.get(`/grafo/${documentoId}/grafo-visual`),
  verUbicaciones: (documentoId) =>
    client.get(`/grafo/${documentoId}/citas/ubicaciones`, { timeout: 120000 }),
  subirPaperManual: (documentoId, referenciaId, archivo) => {
    const form = new FormData()
    form.append('archivo', archivo)
    return client.post(`/grafo/${documentoId}/referencias/${referenciaId}/paper`, form, {
      timeout: 120000,
    })
  },
  quitarPaper: (documentoId, referenciaId) =>
    client.delete(`/grafo/${documentoId}/referencias/${referenciaId}/paper`),

  // CRUD de revisión humana — citas
  actualizarCita: (documentoId, citaId, data) =>
    client.patch(`/grafo/${documentoId}/citas/${citaId}`, data),
  crearCita: (documentoId, data) => client.post(`/grafo/${documentoId}/citas`, data),
  eliminarCita: (documentoId, citaId) =>
    client.delete(`/grafo/${documentoId}/citas/${citaId}`),

  // CRUD de revisión humana — referencias
  actualizarReferencia: (documentoId, refId, data) =>
    client.patch(`/grafo/${documentoId}/referencias/${refId}`, data),
  crearReferencia: (documentoId, data) =>
    client.post(`/grafo/${documentoId}/referencias`, data),
  eliminarReferencia: (documentoId, refId) =>
    client.delete(`/grafo/${documentoId}/referencias/${refId}`),

  reVincular: (documentoId) => client.post(`/grafo/${documentoId}/re-vincular`),
}

// ── Recuperación ─────────────────────────────────────────
export const recuperacionAPI = {
  estadoMotor: (documentoId) => client.get(`/recuperacion/${documentoId}/motor/estado`),
  consultarCita: (documentoId, citaId) =>
    client.get(`/recuperacion/${documentoId}/motor/cita/${citaId}`),
  verEvidencia: (documentoId, citaId) =>
    client.get(`/recuperacion/${documentoId}/motor/evidencia/${citaId}`),
  consultarTextoLibre: (documentoId, texto, nResultados = 3) =>
    client.post(`/recuperacion/${documentoId}/motor/consultar`, {
      texto,
      n_resultados: nResultados,
    }),
}

// ── Auditoría ────────────────────────────────────────────
// Timeout extendido: auditoría + RAGAS llaman al LLM por cada cita.
const AUDITORIA_TIMEOUT = 600000

export const auditoriaAPI = {
  auditar: (documentoId) =>
    client.post(`/auditoria/${documentoId}/auditar`, null, { timeout: AUDITORIA_TIMEOUT }),
  verVeredictos: (documentoId) =>
    client.get(`/auditoria/${documentoId}/veredictos`, { timeout: AUDITORIA_TIMEOUT }),
  verAlertas: (documentoId) => client.get(`/auditoria/${documentoId}/alertas`),
  verAlertasAlucinaciones: (documentoId) =>
    client.get(`/auditoria/${documentoId}/alertas/alucinaciones`),
  evaluarRagas: (documentoId) =>
    client.post(`/auditoria/${documentoId}/evaluar-ragas`, null, { timeout: AUDITORIA_TIMEOUT }),
  verMetricas: (documentoId) => client.get(`/auditoria/${documentoId}/metricas`),
  // El informe del usuario va SIN métricas RAGAS; el admin exporta con ellas.
  exportarInformeExcel: (documentoId, incluirRagas = false) =>
    client.get(`/auditoria/${documentoId}/metricas/exportar`, {
      responseType: 'blob',
      params: incluirRagas ? { incluir_ragas: true } : undefined,
    }),
}

// ── Evaluación vs ground truth experto (admin, separado de RAGAS) ──
export const evaluacionAPI = {
  evaluar: (documentoId, etiquetas) =>
    client.post(`/evaluacion/${documentoId}/evaluar`, { etiquetas }),
  verResultados: (documentoId) => client.get(`/evaluacion/${documentoId}/resultados`),
  exportarExcel: (documentoId) =>
    client.get(`/evaluacion/${documentoId}/exportar`, { responseType: 'blob' }),
}

export default client

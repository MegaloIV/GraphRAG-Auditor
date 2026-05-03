import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

const client = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
})

// Interceptor para logging de errores
client.interceptors.response.use(
  response => response,
  error => {
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

// ── Endpoints de Ingesta ─────────────────────────────────
export const ingestaAPI = {
  cargarPDF: (archivo) => {
    const form = new FormData()
    form.append('archivo', archivo)
    return client.post('/ingesta/cargar', form)
  },

  verEstructura: (documentoId) =>
    client.get(`/ingesta/${documentoId}/estructura`),

  iniciarAuditoria: (documentoId) =>
    client.post(`/ingesta/${documentoId}/iniciar-auditoria`),

  verProgreso: (documentoId) =>
    client.get(`/ingesta/${documentoId}/progreso`),
}

// ── Endpoints del Grafo ──────────────────────────────────
export const grafoAPI = {
  verReferencias: (documentoId) =>
    client.get(`/grafo/${documentoId}/referencias`),

  verCitas: (documentoId) =>
    client.get(`/grafo/${documentoId}/citas`),

  verResumen: (documentoId) =>
    client.get(`/grafo/${documentoId}/resumen`),
}
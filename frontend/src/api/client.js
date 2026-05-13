import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

const client = axios.create({
  baseURL: API_BASE,
  timeout: 300000, // aumentado a 60s para auditoría con LLM
})

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

// ── Ingesta ──────────────────────────────────────────────
export const ingestaAPI = {
  cargarPDF: (archivo) => {
    const form = new FormData()
    form.append('archivo', archivo)
    return client.post('/ingesta/cargar', form)
  },
  verEstructura: (documentoId) =>
    client.get(`/ingesta/${documentoId}/estructura`),
  verProgreso: (documentoId) =>
    client.get(`/ingesta/${documentoId}/progreso`),
}

// ── Grafo ────────────────────────────────────────────────
export const grafoAPI = {
  verReferencias: (documentoId) =>
    client.get(`/grafo/${documentoId}/referencias`),
  verCitas: (documentoId) =>
    client.get(`/grafo/${documentoId}/citas`),
  verResumen: (documentoId) =>
    client.get(`/grafo/${documentoId}/resumen`),
}

// ── Recuperación (EP-003) ────────────────────────────────
export const recuperacionAPI = {
  estadoMotor: (documentoId) =>
    client.get(`/recuperacion/${documentoId}/motor/estado`),
  consultarCita: (documentoId, citaId) =>
    client.get(`/recuperacion/${documentoId}/motor/cita/${citaId}`),
  consultarTextoLibre: (documentoId, texto, nResultados = 3) =>
    client.post(`/recuperacion/${documentoId}/motor/consultar`, {
      texto,
      n_resultados: nResultados,
    }),
}

// ── Auditoría (EP-004) ───────────────────────────────────
export const auditoriaAPI = {
  auditar: (documentoId) =>
    client.post(`/auditoria/${documentoId}/auditar`),
  verVeredictos: (documentoId) =>
    client.get(`/auditoria/${documentoId}/veredictos`),
  verAlertas: (documentoId) =>
    client.get(`/auditoria/${documentoId}/alertas`),
  verAlertasAlucinaciones: (documentoId) =>
    client.get(`/auditoria/${documentoId}/alertas/alucinaciones`),
}
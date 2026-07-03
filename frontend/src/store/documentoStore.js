// Documento activo + documentos recientes, persistidos en localStorage
// (el backend opera por documento_id, no lista documentos).

const CLAVE_RECIENTES = 'graphrag-recientes'
const MAX_RECIENTES = 8

export function listarRecientes() {
  try {
    return JSON.parse(localStorage.getItem(CLAVE_RECIENTES)) || []
  } catch {
    return []
  }
}

export function registrarDocumento({ documentoId, nombreArchivo, paginas }) {
  const recientes = listarRecientes().filter((d) => d.documentoId !== documentoId)
  recientes.unshift({
    documentoId,
    nombreArchivo,
    paginas,
    cargadoEn: new Date().toISOString(),
  })
  localStorage.setItem(CLAVE_RECIENTES, JSON.stringify(recientes.slice(0, MAX_RECIENTES)))
}

export function obtenerDocumento(documentoId) {
  return listarRecientes().find((d) => d.documentoId === documentoId) || null
}

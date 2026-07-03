// Normalización para búsqueda insensible a tildes y mayúsculas (D6).
// SOLO se usa para comparar: nunca se reescribe el texto original.

export function quitarAcentos(s) {
  return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
}

export function normalizarBusqueda(s) {
  return quitarAcentos(String(s || '').toLowerCase()).replace(/\s+/g, ' ').trim()
}

export function coincide(texto, query) {
  return normalizarBusqueda(texto).includes(normalizarBusqueda(query))
}

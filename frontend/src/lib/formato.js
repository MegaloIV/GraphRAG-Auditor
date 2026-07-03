// Utilidades de formato y descarga.

export function formatearRef(ref) {
  const autor = ref.autores?.[0] ? ref.autores[0].split(',')[0] : 'Sin autor'
  const extra = ref.autores?.length > 1 ? ' et al.' : ''
  return `${autor}${extra}${ref.anio ? ` (${ref.anio})` : ''}`
}

export function descargarBlob(blob, nombreArchivo) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = nombreArchivo
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function pct(valor) {
  return valor == null ? '—' : `${Math.round(valor * 100)}%`
}

export function num(valor, decimales = 3) {
  return valor == null ? '—' : Number(valor).toFixed(decimales)
}

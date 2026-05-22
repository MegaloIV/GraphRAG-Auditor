// Store simple para el color de acento
// Usa localStorage para persistir entre sesiones

const ACCENTS = [
  { nombre: 'Azul',     valor: '#3B82F6', hover: '#2563EB' },
  { nombre: 'Esmeralda', valor: '#10B981', hover: '#059669' },
  { nombre: 'Violeta',  valor: '#8B5CF6', hover: '#7C3AED' },
  { nombre: 'Naranja',  valor: '#F59E0B', hover: '#D97706' },
  { nombre: 'Rosa',     valor: '#EC4899', hover: '#DB2777' },
]

function aplicarAccent(valor, hover) {
  document.documentElement.style.setProperty('--accent', valor)
  document.documentElement.style.setProperty('--accent-hover', hover)
  document.documentElement.style.setProperty(
    '--accent-subtle',
    valor + '1F' // 12% opacity en hex
  )
}

function cargarAccent() {
  const guardado = localStorage.getItem('graphrag-accent')
  if (guardado) {
    const accent = ACCENTS.find(a => a.valor === guardado)
    if (accent) aplicarAccent(accent.valor, accent.hover)
  }
}

function cambiarAccent(valor) {
  const accent = ACCENTS.find(a => a.valor === valor)
  if (!accent) return
  localStorage.setItem('graphrag-accent', valor)
  aplicarAccent(accent.valor, accent.hover)
}

// Cargar al iniciar
cargarAccent()

export { ACCENTS, cambiarAccent, cargarAccent }
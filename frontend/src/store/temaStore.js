// Tema claro (default, D10) / oscuro conmutable, persistido en localStorage.

const CLAVE = 'graphrag-tema'

function aplicarTema(tema) {
  document.documentElement.setAttribute('data-tema', tema)
}

export function cargarTema() {
  const guardado = localStorage.getItem(CLAVE) || 'claro'
  aplicarTema(guardado)
  return guardado
}

export function alternarTema() {
  const actual = localStorage.getItem(CLAVE) || 'claro'
  const nuevo = actual === 'claro' ? 'oscuro' : 'claro'
  localStorage.setItem(CLAVE, nuevo)
  aplicarTema(nuevo)
  return nuevo
}

cargarTema()

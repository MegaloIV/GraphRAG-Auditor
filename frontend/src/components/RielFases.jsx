import { useNavigate } from 'react-router-dom'
import { Check, Lock } from 'lucide-react'
import { FASES, faseMaxima } from '../lib/fases'

export default function RielFases({ documentoId, estado, faseActiva }) {
  const navigate = useNavigate()
  const maxima = faseMaxima(estado)

  return (
    <nav className="riel">
      {FASES.map((fase, i) => {
        const bloqueada = i > maxima
        const activa = fase.clave === faseActiva
        const completa = !bloqueada && !activa && i < maxima
        return (
          <button
            key={fase.clave}
            className={`riel-fase ${activa ? 'activa' : ''} ${completa ? 'completa' : ''}`}
            disabled={bloqueada}
            onClick={() => navigate(`/doc/${documentoId}/${fase.clave}`)}
          >
            <span className="riel-num">
              {completa ? <Check size={13} /> : bloqueada ? <Lock size={11} /> : i + 1}
            </span>
            {fase.nombre}
          </button>
        )
      })}
    </nav>
  )
}

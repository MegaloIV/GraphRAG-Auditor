import { useState } from 'react'
import { ACCENTS, cambiarAccent } from '../../store/accentStore'

export default function Navbar({ onVolver, mostrarVolver = false }) {
  const [accentActual, setAccentActual] = useState(
    localStorage.getItem('graphrag-accent') || '#3B82F6'
  )

  const handleAccent = (valor) => {
    cambiarAccent(valor)
    setAccentActual(valor)
  }

  return (
    <nav style={{
      background: 'var(--bg-surface)',
      borderBottom: '1px solid var(--border)',
      padding: '0 2rem',
      height: '60px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      position: 'sticky',
      top: 0,
      zIndex: 100,
    }}>
      {/* Logo */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        {mostrarVolver && (
          <button
            onClick={onVolver}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              fontSize: '1.2rem',
              padding: '0.25rem',
              marginRight: '0.5rem',
            }}
          >
            ←
          </button>
        )}
        <div style={{
          width: '32px',
          height: '32px',
          background: 'var(--accent)',
          borderRadius: 'var(--radius-sm)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '1rem',
        }}>
          ⬡
        </div>
        <div>
          <div style={{
            fontWeight: 700,
            fontSize: '0.95rem',
            color: 'var(--text-primary)',
            lineHeight: 1,
          }}>
            GraphRAG
          </div>
          <div style={{
            fontSize: '0.7rem',
            color: 'var(--text-muted)',
            lineHeight: 1,
            marginTop: '2px',
          }}>
            Auditor Semántico
          </div>
        </div>
      </div>

      {/* Selector de accent */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          Color
        </span>
        {ACCENTS.map(accent => (
          <button
            key={accent.valor}
            onClick={() => handleAccent(accent.valor)}
            title={accent.nombre}
            style={{
              width: '20px',
              height: '20px',
              borderRadius: '50%',
              background: accent.valor,
              border: accentActual === accent.valor
                ? '2px solid white'
                : '2px solid transparent',
              cursor: 'pointer',
              transition: 'transform 0.15s ease',
              transform: accentActual === accent.valor ? 'scale(1.2)' : 'scale(1)',
            }}
          />
        ))}
      </div>
    </nav>
  )
}
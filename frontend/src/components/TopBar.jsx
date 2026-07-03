import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { BookOpenCheck, Moon, Sun, FilePlus2 } from 'lucide-react'
import { saludAPI } from '../api/client'
import { alternarTema, cargarTema } from '../store/temaStore'
import { ACCENTS, cambiarAccent } from '../store/accentStore'

function SelectorAccent() {
  return (
    <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
      {ACCENTS.map((a) => (
        <button
          key={a.valor}
          title={a.nombre}
          onClick={() => cambiarAccent(a.valor)}
          style={{
            width: 14, height: 14, borderRadius: '50%', cursor: 'pointer',
            background: a.valor, border: '2px solid var(--bg-surface)',
            boxShadow: '0 0 0 1px var(--border-strong)', padding: 0,
          }}
        />
      ))}
    </div>
  )
}

export default function TopBar({ nombreDocumento }) {
  const [tema, setTema] = useState(cargarTema())
  const [salud, setSalud] = useState('verificando')
  const navigate = useNavigate()

  useEffect(() => {
    let vivo = true
    const verificar = () =>
      saludAPI.verificar()
        .then(() => vivo && setSalud('ok'))
        .catch(() => vivo && setSalud('mal'))
    verificar()
    const intervalo = setInterval(verificar, 30000)
    return () => { vivo = false; clearInterval(intervalo) }
  }, [])

  return (
    <header className="topbar">
      <Link to="/" className="topbar-marca">
        <BookOpenCheck size={21} color="var(--accent)" />
        GraphAuditor
      </Link>

      {nombreDocumento && <span className="topbar-doc">📄 {nombreDocumento}</span>}

      <div style={{ flex: 1 }} />

      <span className="salud">
        <span className={`salud-punto ${salud === 'verificando' ? '' : salud}`} />
        {salud === 'ok' ? 'backend ok' : salud === 'mal' ? 'backend sin conexión' : 'verificando…'}
      </span>

      <SelectorAccent />

      <button
        className="btn-icono"
        title={tema === 'claro' ? 'Modo oscuro' : 'Modo claro'}
        onClick={() => setTema(alternarTema())}
      >
        {tema === 'claro' ? <Moon size={16} /> : <Sun size={16} />}
      </button>

      <button className="btn btn-contorno" onClick={() => navigate('/')}>
        <FilePlus2 size={15} />
        Nuevo documento
      </button>
    </header>
  )
}

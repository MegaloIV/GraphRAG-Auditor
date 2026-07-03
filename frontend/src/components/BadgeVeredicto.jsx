import { CheckCircle2, XCircle, HelpCircle } from 'lucide-react'

// Único punto que define color + ícono + texto de cada veredicto.
const VEREDICTOS = {
  SUPPORTS: { clase: 'badge-supports', Icono: CheckCircle2, texto: 'Respaldada' },
  REFUTES:  { clase: 'badge-refutes',  Icono: XCircle,      texto: 'Refutada' },
  NO_INFO:  { clase: 'badge-noinfo',   Icono: HelpCircle,   texto: 'Sin evidencia' },
}

export default function BadgeVeredicto({ veredicto, soloCodigo = false }) {
  const v = VEREDICTOS[veredicto]
  if (!v) return <span className="badge badge-neutro">Sin auditar</span>
  const { clase, Icono, texto } = v
  return (
    <span className={`badge ${clase}`}>
      <Icono size={13} />
      {soloCodigo ? veredicto : texto}
    </span>
  )
}

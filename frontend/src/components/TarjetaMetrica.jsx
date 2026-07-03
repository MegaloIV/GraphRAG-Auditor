export default function TarjetaMetrica({ etiqueta, valor, detalle, color }) {
  return (
    <div className="tarjeta-metrica">
      <div className="etiqueta">{etiqueta}</div>
      <div className="valor" style={color ? { color } : undefined}>{valor}</div>
      {detalle && <div className="detalle">{detalle}</div>}
    </div>
  )
}

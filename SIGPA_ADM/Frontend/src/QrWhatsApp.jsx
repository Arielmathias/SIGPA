import React, { useEffect, useMemo, useRef } from 'react';
import qrcode from 'qrcode-generator';

// Dibuja el QR como un único path SVG: nítido a cualquier tamaño y sin insertar HTML.
export function Qr({ texto, titulo }) {
  const { tam, d } = useMemo(() => {
    const qr = qrcode(0, 'M');
    qr.addData(texto);
    qr.make();
    const n = qr.getModuleCount();
    let d = '';
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (qr.isDark(y, x)) d += `M${x + 2} ${y + 2}h1v1h-1z`;
    return { tam: n + 4, d };
  }, [texto]);
  return <svg className="lp-qr" viewBox={`0 0 ${tam} ${tam}`} role="img" aria-label={titulo} shapeRendering="crispEdges"><rect width={tam} height={tam} fill="#fff"/><path d={d} fill="#141a63"/></svg>;
}

// Ventana con el QR de un producto: se escanea con el celular y abre WhatsApp con el pedido escrito.
export function VentanaQr({ producto, mensaje, enlace, onClose }) {
  const cerrar = useRef(null);
  useEffect(() => {
    const previo = document.activeElement;
    cerrar.current?.focus();
    const tecla = e => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', tecla);
    return () => { document.removeEventListener('keydown', tecla); previo?.focus?.(); };
  }, [onClose]);
  return <div className="lp-velo" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="lp-ventana" role="dialog" aria-modal="true" aria-labelledby="qr-titulo">
      <button ref={cerrar} type="button" className="lp-cerrar" onClick={onClose} aria-label="Cerrar">×</button>
      <h3 id="qr-titulo">Pide {producto}</h3>
      <p>Escanea el código con la cámara de tu celular y se abrirá WhatsApp con este mensaje:</p>
      <blockquote>{mensaje}</blockquote>
      <Qr texto={enlace} titulo={`Código QR para pedir ${producto} por WhatsApp`}/>
      <a className="lp-btn lp-btn-wsp" href={enlace} target="_blank" rel="noopener noreferrer">O ábrelo en WhatsApp Web</a>
    </div>
  </div>;
}

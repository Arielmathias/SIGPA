import React, { useCallback, useState } from 'react';
import { BIDONES, COMUNAS, DISPENSADORES, PACKS, REPARTO, SITIO, WHATSAPP_VISIBLE, enlaceWhatsApp, pesos } from './marca';
import { Qr, VentanaQr } from './QrWhatsApp';
import './landing.css';

const mensajePedido = producto => `Hola, quiero pedir: ${producto}`;
// En computador no hay WhatsApp a mano: se muestra un QR para escanear con el celular.
const esEscritorio = () => window.matchMedia?.('(hover: hover) and (pointer: fine)').matches;

function Pedir({ producto, onQr, children }) {
  return <a className="lp-pedir" href={enlaceWhatsApp(mensajePedido(producto))} target="_blank" rel="noopener noreferrer"
    onClick={e => { if (esEscritorio()) { e.preventDefault(); onQr(producto); } }}>{children}</a>;
}

function WhatsAppIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2Zm0 18.2a8.2 8.2 0 0 1-4.2-1.2l-.3-.2-3 .8.8-2.9-.2-.3A8.2 8.2 0 1 1 12 20.2Zm4.5-6.1c-.2-.1-1.5-.7-1.7-.8-.2-.1-.4-.1-.6.1l-.8 1c-.1.2-.3.2-.5.1a6.7 6.7 0 0 1-3.3-2.9c-.2-.4.2-.4.7-1.3.1-.2 0-.3 0-.4l-.8-1.8c-.2-.5-.4-.4-.6-.4h-.5a1 1 0 0 0-.7.3 3 3 0 0 0-.9 2.2 5.2 5.2 0 0 0 1.1 2.7 11.9 11.9 0 0 0 4.6 4c1.7.7 2.4.8 3.2.7.5-.1 1.5-.6 1.8-1.2.2-.6.2-1.1.1-1.2l-.5-.3Z"/></svg>;
}

function Ola({ className }) {
  // La ola verde sobre azul de las piezas gráficas de Agua DM
  return <svg className={className} aria-hidden="true" viewBox="0 0 1440 140" preserveAspectRatio="none"><path className="ola-verde" d="M0 70C240 10 420 10 640 52s420 70 800-30v118H0Z"/><path className="ola-azul" d="M0 98C260 40 440 44 660 78s420 50 780-38v100H0Z"/></svg>;
}

function Precio({ valor, children }) {
  return <span className="lp-precio"><small>{children}</small>{pesos(valor)}</span>;
}

export default function Landing({ onAdmin }) {
  const hoy = new Date().getDay();
  const [qr, setQr] = useState(null);
  const cerrarQr = useCallback(() => setQr(null), []);
  return <div className="lp">
    <header className="lp-top">
      <a className="lp-logo" href="#inicio" aria-label="Agua DM, inicio"><img src="/marca/logo.jpg" alt="DM Agua Purificada" width="400" height="393"/></a>
      <nav aria-label="Secciones"><a href="#precios">Precios</a><a href="#reparto">Días de reparto</a><a href="#contacto">Contacto</a></nav>
      <a className="lp-btn lp-btn-wsp lp-top-cta" href={enlaceWhatsApp()} target="_blank" rel="noopener noreferrer"><WhatsAppIcon/>Pedir</a>
      <button type="button" className="lp-admin" onClick={onAdmin}>Admin</button>
    </header>

    <main>
      <section className="lp-hero" id="inicio">
        <div className="lp-hero-texto">
          <p className="lp-kicker">Agua purificada · Despacho gratuito</p>
          <h1>Tu agua, en la puerta de tu casa.</h1>
          <p className="lp-lead">Llevamos bidones de 12 y 20 litros de lunes a viernes a {COMUNAS.slice(0, -1).join(', ')} y {COMUNAS.at(-1)}.</p>
          <div className="lp-acciones">
            <a className="lp-btn lp-btn-wsp" href={enlaceWhatsApp('Hola, quiero hacer un pedido de agua')} target="_blank" rel="noopener noreferrer"><WhatsAppIcon/>Pedir por WhatsApp</a>
            <a className="lp-btn lp-btn-linea" href="#precios">Ver precios</a>
          </div>
        </div>
        <img className="lp-hero-foto" src="/marca/productos.jpg" alt="Bidones de 20 y 12 litros, botella de 6 litros, dispensador USB y dispensador básico de Agua DM" width="845" height="420"/>
      </section>

      <Ola className="lp-ola"/>

      <section className="lp-reparto" id="reparto" aria-labelledby="reparto-titulo">
        <div className="lp-reparto-texto">
          <h2 id="reparto-titulo">¿Qué día pasamos por tu sector?</h2>
          <ol className="lp-semana">
            {REPARTO.map(r => <li key={r.dia} className={r.dia === hoy ? 'hoy' : ''}>
              <span className="lp-dia">{r.nombre}{r.dia === hoy && <em>Hoy</em>}</span>
              <span className="lp-zona">{r.zona}</span>
            </li>)}
          </ol>
          <p className="lp-nota">¿Estás en Reñaca o en otro sector? <a href={enlaceWhatsApp('Hola, ¿qué día reparten en mi sector?')} target="_blank" rel="noopener noreferrer">Escríbenos</a> y te confirmamos el día.</p>
        </div>
        <figure className="lp-repartidor">
          <img src="/marca/repartidor.jpg" alt="Repartidor de Agua DM con un bidón de 20 litros al hombro" width="440" height="640" loading="lazy"/>
          <figcaption>El despacho a domicilio no tiene costo.</figcaption>
        </figure>
      </section>

      <section className="lp-precios" id="precios" aria-labelledby="precios-titulo">
        <h2 id="precios-titulo">Precios</h2>
        <p className="lp-sub">Valores referenciales: te los confirmamos al tomar tu pedido.</p>

        <h3>Bidones</h3>
        <div className="lp-grid lp-grid-2">
          {BIDONES.map(b => <article key={b.id} className="lp-card lp-card-foto">
            <img src={b.imagen} alt={b.nombre} loading="lazy"/>
            <div><h4>{b.nombre}</h4>{b.precios.map(([t, v]) => <Precio key={t} valor={v}>{t}</Precio>)}
              <Pedir producto={b.nombre} onQr={setQr}>Pedir {b.nombre} →</Pedir></div>
          </article>)}
        </div>

        <h3>Packs <span>incluyen despacho</span></h3>
        <div className="lp-grid lp-grid-3">
          {PACKS.map(p => <article key={p.id} className="lp-card lp-card-pack">
            <img src={p.imagen} alt="" loading="lazy"/>
            <h4>{p.nombre}</h4><p>{p.detalle}</p><Precio valor={p.precio}/>
            <Pedir producto={p.nombre} onQr={setQr}>Pedir este pack →</Pedir>
          </article>)}
        </div>

        <h3>Dispensadores</h3>
        <div className="lp-grid lp-grid-2">
          {DISPENSADORES.map(d => <article key={d.id} className="lp-card lp-card-foto">
            <img src={d.imagen} alt={d.nombre} loading="lazy"/>
            <div><h4>{d.nombre}</h4><p>{d.detalle}</p><Precio valor={d.precio}/>
              <Pedir producto={d.nombre} onQr={setQr}>Pedir {d.nombre} →</Pedir></div>
          </article>)}
        </div>
      </section>

      <section className="lp-contacto" id="contacto" aria-labelledby="contacto-titulo">
        <h2 id="contacto-titulo">Pide por WhatsApp</h2>
        <p>Cuéntanos qué necesitas y tu dirección. Te confirmamos el día de reparto de tu sector.</p>
        <div className="lp-contacto-cta"><a className="lp-numero" href={enlaceWhatsApp()} target="_blank" rel="noopener noreferrer"><WhatsAppIcon/>{WHATSAPP_VISIBLE}</a><figure className="lp-qr-contacto"><Qr texto={enlaceWhatsApp('Hola, quiero hacer un pedido de agua')} titulo="Código QR para escribirnos por WhatsApp"/><figcaption>Escanéalo con tu celular</figcaption></figure></div>
      </section>
    </main>

    <footer className="lp-pie">
      <img src="/marca/logo.jpg" alt="DM Agua Purificada" width="400" height="393" loading="lazy"/>
      <div><strong>Zonas de reparto</strong><p>{COMUNAS.join(' · ')}</p></div>
      <div><strong>Contacto</strong><p><a href={enlaceWhatsApp()} target="_blank" rel="noopener noreferrer">{WHATSAPP_VISIBLE}</a><br/><a href={`https://${SITIO}`} target="_blank" rel="noopener noreferrer">{SITIO}</a></p></div>
      <button type="button" className="lp-admin" onClick={onAdmin}>Acceso administrativo</button>
    </footer>
    {qr && <VentanaQr producto={qr} mensaje={mensajePedido(qr)} enlace={enlaceWhatsApp(mensajePedido(qr))} onClose={cerrarQr}/>}
  </div>;
}

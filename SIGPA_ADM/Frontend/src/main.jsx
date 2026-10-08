import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { login, logout, onSessionExpired, restoreSession } from './auth';
import { api } from './api';
import { moveStop } from './route';
import Landing from './Landing';
import './styles.css';

const money = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(n);
const name = c => [c.nombre, c.apellido_paterno, c.apellido_materno].filter(Boolean).join(' ');
function Brand({ blanco }) { return <span className="brand"><img src={blanco ? '/marca/logo-blanco.svg' : '/marca/logo.svg'} alt="Agua DM" width="2320" height="2280"/><span>SIGPA<small>Panel de operación</small></span></span>; }
function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState('inicio');
  useEffect(() => {
    let active = true;
    const unsubscribe = onSessionExpired(() => { setSession(null); setPage('login'); });
    restoreSession().then(user => { if (active) { setSession(user ? { user } : null); setLoading(false); } });
    return () => { active = false; unsubscribe(); };
  }, []);
  if (loading) return <p className="loading">Cargando sesión…</p>;
  if (session) return <Dashboard key={session.user.id} session={session} onLogout={() => { setSession(null); setPage('inicio'); }}/>;
  if (page === 'inicio') return <Landing onAdmin={() => { setPage('login'); window.scrollTo(0, 0); }}/>;
  return <><header><Brand/><button onClick={() => setPage('inicio')}>Volver al inicio</button></header><Login onLogin={user => setSession({ user })}/></>;
}
function Login({ onLogin }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault(); setBusy(true); setError('');
    const form = new FormData(e.currentTarget);
    try { onLogin(await login(form.get('email'), form.get('password'))); }
    catch (error) { setError(error.message || 'No se pudo iniciar sesión. Revisa el correo, la contraseña y la conexión.'); setBusy(false); }
  }
  return <main className="login"><form onSubmit={submit}><span className="eyebrow">Bienvenido a SIGPA</span><h1>Acceso al equipo</h1><p>Ingresa con tu cuenta autorizada.</p><label>Correo electrónico<input autoComplete="username" name="email" type="email" required/></label><label>Contraseña<input autoComplete="current-password" name="password" type="password" required/></label>{error && <p role="alert" className="error">{error}</p>}<button disabled={busy} className="primary">{busy ? 'Ingresando…' : 'Ingresar'}</button></form></main>;
}
function Dashboard({ session, onLogout }) {
  const [tab, setTab] = useState('Resumen');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [selected, setSelected] = useState([]);
  const [result, setResult] = useState(null);
  const [manual, setManual] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError('');
    Promise.all(['/pedidos', '/clientes', '/rutas/pedidos-pendientes'].map(p => api(p, { signal: controller.signal })))
      .then(([pedidos, clientes, pendientes]) => { if (![pedidos, clientes, pendientes].every(Array.isArray)) throw new Error('Formato de datos inesperado.'); setData({ pedidos, clientes, pendientes }); setSelected(ids => ids.filter(id => pendientes.some(p => p.pedido_id === id))); })
      .catch(e => { if (e.name !== 'AbortError') { setError(e.message); setData(null); } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [revision]);
  async function plan() {
    if (!window.confirm(`Se planificarán y confirmarán ${selected.length} pedidos. El backend guardará el orden obtenido. ¿Continuar?`)) return;
    setPlanning(true); setError('');
    try { const value = await api('/rutas/planificar', { method: 'POST', body: JSON.stringify({ pedido_ids: selected }) }); setResult(value); setManual(false); setSelected([]); setRevision(r => r + 1); }
    catch(e) { setError(`${e.message} Actualiza los pedidos antes de volver a intentar; si se interrumpió la conexión, la operación podría haber terminado en el servidor.`); }
    finally { setPlanning(false); }
  }
  async function view(id) { setDetail(null); try { setDetail(await api(`/pedidos/${id}`)); } catch(e) { setError(e.message); } }
  const pedidos = (data?.pedidos || []).filter(p => (!status || p.estado === status) && `${p.id} ${p.cliente_nombre || ''} ${p.comuna_nombre || ''}`.toLowerCase().includes(query.toLowerCase()));
  const clientes = (data?.clientes || []).filter(c => `${name(c)} ${c.telefono || ''}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="shell"><aside><Brand blanco/><div className="nav-label">Operación</div>{['Resumen','Pedidos','Clientes','Rutas'].map(t => <button key={t} className={tab === t ? 'active' : ''} onClick={() => { setTab(t); setQuery(''); setStatus(''); }}>{t}</button>)}<div className="account"><small>{session.user.email}</small><button onClick={async () => { await logout(); onLogout(); }}>Cerrar sesión</button></div></aside><main className="workspace"><header><span>Panel administrativo</span><span className="badge">Agua DM</span></header><div className="content"><div className="title"><div><span className="eyebrow">Centro de operaciones</span><h1>{tab}</h1><p>Consulta y organiza la información de SIGPA.</p></div><button disabled={busy || planning} onClick={() => setRevision(r => r + 1)}>Actualizar datos</button></div>{error && <p role="alert" className="error">{error}</p>}{busy && <p role="status">Cargando información…</p>}{data && <>
  {tab === 'Resumen' && <><div className="kpis">{[['Pedidos registrados',data.pedidos.length],['Pendientes',data.pedidos.filter(p=>p.estado==='pendiente').length],['Confirmados',data.pedidos.filter(p=>p.estado==='confirmado').length],['Clientes activos',data.clientes.filter(c=>c.activo).length]].map(([label,n]) => <article key={label}><span>{label}</span><strong>{n}</strong></article>)}</div><p className="muted">Totales de todos los registros disponibles, sin filtro de fecha.</p><section><h2>Últimos pedidos</h2><OrderTable rows={data.pedidos.slice(0,8)} view={view}/></section></>}
  {tab === 'Pedidos' && <section><div className="filters"><input aria-label="Buscar pedidos" placeholder="Buscar cliente, comuna o ID…" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="Estado del pedido" value={status} onChange={e=>setStatus(e.target.value)}><option value="">Todos los estados</option>{['pendiente','confirmado','en_despacho','entregado','cancelado'].map(s=><option key={s}>{s}</option>)}</select></div><OrderTable rows={pedidos} view={view}/></section>}
  {tab === 'Clientes' && <section><input aria-label="Buscar clientes" placeholder="Buscar nombre o teléfono…" value={query} onChange={e=>setQuery(e.target.value)}/><div className="table"><table><thead><tr><th>Cliente</th><th>Teléfono</th><th>Dirección</th><th>Día de reparto</th><th>Estado</th></tr></thead><tbody>{clientes.map(c=><tr key={c.id}><td>{name(c)}</td><td>{c.telefono || '—'}</td><td>{c.direccion || 'Sin dirección'}</td><td>{c.dia_reparto || 'Sin asignar'}</td><td>{c.activo ? 'Activo' : 'Inactivo'}</td></tr>)}</tbody></table>{!clientes.length && <p>No se encontraron clientes.</p>}</div></section>}
  {tab === 'Rutas' && <><section className="no-print"><h2>Preparar ruta</h2><p>Selecciona los pedidos pendientes que se incluirán. Se muestran todas las fechas: el backend todavía no registra una fecha de entrega.</p>{data.pendientes.map(p=><label className="stop" key={p.pedido_id}><input type="checkbox" disabled={planning} checked={selected.includes(p.pedido_id)} onChange={e=>setSelected(ids=>e.target.checked ? [...ids,p.pedido_id] : ids.filter(id=>id!==p.pedido_id))}/><span><strong>#{p.pedido_id} · {p.cliente_nombre}</strong><small>{p.direccion_texto || 'Sin dirección'} · {new Date(p.creado_en).toLocaleDateString('es-CL',{timeZone:'America/Santiago'})}</small></span></label>)}{!data.pendientes.length && <p>No hay pedidos pendientes.</p>}<button className="primary" disabled={!selected.length || planning || busy} onClick={plan}>{planning ? 'Planificando…' : `Planificar y confirmar (${selected.length})`}</button></section>{result && <section className="print-route"><div className="title"><h2>Orden de reparto</h2><button className="no-print" disabled={!result.ruta.length} onClick={()=>window.print()}>Imprimir ruta</button></div><p className="notice">{manual ? 'Orden modificado para esta impresión. Los cambios manuales NO están guardados en el servidor y se pierden al salir o recargar.' : 'Orden generado y guardado por el backend. Puedes ajustarlo para esta impresión.'}</p>{result.ruta.map((p,i)=><div className="stop" key={p.pedido_id}><b className="number">{i+1}</b><span><strong>#{p.pedido_id} · {p.cliente_nombre}</strong><small>{p.direccion_texto || 'Sin dirección'}</small></span><div className="no-print"><button aria-label={`Subir pedido ${p.pedido_id}`} disabled={i===0} onClick={()=>{setResult(r=>({...r,ruta:moveStop(r.ruta,i,-1)}));setManual(true);}}>↑</button><button aria-label={`Bajar pedido ${p.pedido_id}`} disabled={i===result.ruta.length-1} onClick={()=>{setResult(r=>({...r,ruta:moveStop(r.ruta,i,1)}));setManual(true);}}>↓</button></div></div>)}{result.sin_resolver?.length > 0 && <div className="error"><h3>Pedidos sin ubicación resuelta</h3>{result.sin_resolver.map(p=><p key={p.pedido_id}>#{p.pedido_id} · {p.cliente_nombre}: {p.motivo}</p>)}</div>}{result.confirmados_con_orden_fuera_de_solicitud?.length > 0 && <p className="notice">Hay {result.confirmados_con_orden_fuera_de_solicitud.length} pedidos confirmados con orden de otras planificaciones. No se incluyen en esta impresión.</p>}</section>}</>}
  </>}{detail && <div className="modal" role="dialog" aria-modal="true" aria-label="Detalle del pedido"><section><button autoFocus onClick={()=>setDetail(null)}>Cerrar detalle</button><h2>Pedido #{detail.id}</h2><p>{detail.cliente_nombre} · {detail.estado}</p><p>{detail.direccion_despacho || 'Sin dirección de despacho informada'}</p>{detail.lineas.map((l,i)=><p key={i}>{l.cantidad_solicitada} × {l.producto_nombre} · {money(l.precio_unitario)}</p>)}<strong>Total: {money(detail.total)}</strong></section></div>}</div></main></div>;
}
function OrderTable({ rows, view }) { return <div className="table"><table><thead><tr><th>Pedido</th><th>Cliente</th><th>Comuna</th><th>Estado</th><th>Total</th><th>Detalle</th></tr></thead><tbody>{rows.map(p=><tr key={p.id}><td>#{p.id}</td><td>{p.cliente_nombre || `Cliente ${p.cliente_id}`}</td><td>{p.comuna_nombre || '—'}</td><td><span className={`badge ${p.estado}`}>{p.estado.replaceAll('_',' ')}</span></td><td>{money(p.total)}</td><td><button onClick={()=>view(p.id)}>Ver</button></td></tr>)}</tbody></table>{!rows.length && <p>No hay pedidos para mostrar.</p>}</div>; }
createRoot(document.getElementById('root')).render(<App/>);

import { supabase } from './supabase';
export async function api(path, { signal, ...options } = {}) {
  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session) throw new Error('La sesión terminó. Vuelve a ingresar.');
  let response;
  try {
    response = await fetch(`/api${path}`, { ...options, signal, headers: { Authorization: `Bearer ${data.session.access_token}`, ...(options.body ? { 'Content-Type': 'application/json' } : {}) } });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('No se pudo conectar con SIGPA. Revisa la conexión y la URL del backend.');
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) throw new Error('Sesión rechazada por el servidor. Cierra sesión y vuelve a ingresar.');
    const detail = body?.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.mensaje || `No se pudo completar la operación (${response.status}).`);
  }
  if (body === null) throw new Error('El servidor devolvió una respuesta inesperada.');
  return body;
}

import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeJwtPayload, getAccessToken, isExpiring, login, onSessionExpired, refresh, restoreSession, userFromToken } from './auth.js';

const jwt = payload => `e30.${Buffer.from(JSON.stringify(payload)).toString('base64url')}.firma`;
const tokens = (n, expires_in = 3600) => ({ access_token: jwt({ sub: `usuario-${n}`, email: 'admin@aguadm.cl' }), refresh_token: `refresh-${n}`, token_type: 'bearer', expires_in });
const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
function mockFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url, body: init.body ? JSON.parse(init.body) : null }); return handler(url, init); };
  return calls;
}

test('decodifica el payload del JWT con base64url (- y _) y UTF-8', () => {
  const payload = { sub: 'abc-123', email: 'señora@aguadm.cl', nota: '??????>>>>>>' };
  const token = jwt(payload);
  const part = token.split('.')[1];
  assert.match(part, /-/); assert.match(part, /_/); assert.doesNotMatch(part, /=/);
  assert.deepEqual(decodeJwtPayload(token), payload);
  assert.deepEqual(userFromToken(token), { id: 'abc-123', email: 'señora@aguadm.cl' });
  assert.throws(() => decodeJwtPayload('sin-puntos'));
});

test('considera expirado el token dentro del margen de 30 s', () => {
  const expiry = 100_000;
  assert.equal(isExpiring(expiry, 69_999), false);
  assert.equal(isExpiring(expiry, 70_000), true);
  assert.equal(isExpiring(expiry, 120_000), true);
  assert.equal(isExpiring(0, 0), true);
  assert.equal(isExpiring(expiry, 89_999, 10_000), false);
});

test('refresh() simultáneos hacen una sola petición y luego usan el token rotado', async () => {
  mockFetch(async () => reply(200, tokens(1, 0)));
  await login('admin@aguadm.cl', 'clave');
  let release; const gate = new Promise(resolve => { release = resolve; });
  let n = 1;
  const calls = mockFetch(async () => { await gate; return reply(200, tokens(++n)); });
  const pending = [refresh(), refresh(), getAccessToken()];
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].body, { refresh_token: 'refresh-1' });
  release();
  const [a, b, token] = await Promise.all(pending);
  assert.deepEqual(a, { id: 'usuario-2', email: 'admin@aguadm.cl' });
  assert.equal(a, b);
  assert.equal(token, tokens(2).access_token);
  await refresh();
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].body, { refresh_token: 'refresh-2' });
});

test('restoreSession conserva la sesión ante 5xx y solo un 401 la cierra', async () => {
  mockFetch(async () => reply(200, tokens(10)));
  await login('admin@aguadm.cl', 'clave');
  mockFetch(async () => reply(503, { detail: 'Servicio de autenticación no disponible' }));
  assert.equal(await restoreSession(), null);
  let expired = 0;
  const off = onSessionExpired(() => expired++);
  const calls = mockFetch(async () => reply(401, { detail: 'Sesión expirada, inicie sesión nuevamente' }));
  assert.equal(await restoreSession(), null);
  assert.deepEqual(calls[0].body, { refresh_token: 'refresh-10' });
  assert.equal(expired, 1);
  assert.equal(await restoreSession(), null);
  assert.equal(calls.length, 1);
  off();
});

test('login muestra el detail del backend en 401 y 429, y mensajes genéricos en 5xx o sin red', async () => {
  for (const [status, detail, message] of [
    [401, 'Correo o contraseña incorrectos', 'Correo o contraseña incorrectos'],
    [429, 'Demasiados intentos, espere un momento', 'Demasiados intentos, espere un momento'],
    [503, 'Servicio de autenticación no disponible', 'El servicio de autenticación no está disponible. Intenta más tarde.'],
  ]) {
    mockFetch(async () => reply(status, { detail }));
    await assert.rejects(login('admin@aguadm.cl', 'clave'), { message });
  }
  globalThis.fetch = async () => { throw new TypeError('fetch failed'); };
  await assert.rejects(login('admin@aguadm.cl', 'clave'), { message: 'No se pudo conectar con SIGPA. Revisa la conexión.' });
});

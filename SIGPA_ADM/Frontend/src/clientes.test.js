import test from 'node:test';
import assert from 'node:assert/strict';
import { cambiosCliente, clienteForm } from './clientes.js';
const cliente = { id: 7, nombre: 'Ana', apellido_paterno: 'Rojas', apellido_materno: null, telefono: '56911111111', direccion: 'Av. Libertad 100', tipo_cliente_id: 1, activo: true, opt_out_whatsapp: false };
test('sin cambios no envía campos', () => {
  assert.deepEqual(cambiosCliente(cliente, clienteForm(cliente)), { cambios: {} });
});
test('envía solo los campos modificados y recorta espacios', () => {
  const form = { ...clienteForm(cliente), direccion: '  Calle 5 #20 ', tipo_cliente_id: '2', activo: false };
  assert.deepEqual(cambiosCliente(cliente, form), { cambios: { direccion: 'Calle 5 #20', tipo_cliente_id: 2, activo: false } });
});
test('un campo opcional vaciado se guarda como null', () => {
  const form = { ...clienteForm(cliente), apellido_paterno: '   ', tipo_cliente_id: '' };
  assert.deepEqual(cambiosCliente(cliente, form), { cambios: { apellido_paterno: null, tipo_cliente_id: null } });
});
test('nombre y teléfono son obligatorios', () => {
  assert.equal(cambiosCliente(cliente, { ...clienteForm(cliente), nombre: ' ' }).error, 'El nombre es obligatorio.');
  assert.equal(cambiosCliente(cliente, { ...clienteForm(cliente), telefono: '' }).error, 'El teléfono es obligatorio.');
});

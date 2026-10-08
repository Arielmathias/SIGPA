import test from 'node:test';
import assert from 'node:assert/strict';
import { indexarPrecios } from './catalogo.js';
import { BIDONES, DISPENSADORES, PRECIOS_RESPALDO, PROMOS } from './marca.js';

test('indexa nombre y precio, sin traer el stock', () => {
  const precios = indexarPrecios([{ id: 1, nombre: 'Bidón 12L Nuevo', precio_unitario: 6000.0, stock: 50, capacidad_litros: 12 }]);
  assert.deepEqual(precios, { 'Bidón 12L Nuevo': 6000 });
});
test('ignora filas sin precio válido', () => {
  assert.deepEqual(indexarPrecios([{ nombre: 'A', precio_unitario: null }, { nombre: 'B', precio_unitario: 'x' }, { precio_unitario: 10 }]), {});
});
test('rechaza una respuesta que no es lista', () => {
  assert.throws(() => indexarPrecios({ detail: 'error' }));
});
test('cada producto de la landing tiene precio de respaldo', () => {
  const nombres = [...BIDONES.flatMap(b => b.precios.map(([, p]) => p)), ...DISPENSADORES.map(d => d.producto), ...PROMOS.map(p => p.producto)];
  for (const nombre of nombres) assert.ok(nombre in PRECIOS_RESPALDO, `falta respaldo para ${nombre}`);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { moveStop } from './route.js';
test('reordenar conserva todos los pedidos y no modifica el original', () => {
  const source = [{ pedido_id: 1 }, { pedido_id: 2 }, { pedido_id: 3 }];
  const moved = moveStop(source, 1, -1);
  assert.deepEqual(moved.map(p => p.pedido_id), [2, 1, 3]);
  assert.deepEqual(source.map(p => p.pedido_id), [1, 2, 3]);
  assert.equal(moveStop(source, 0, -1), source);
  assert.equal(moveStop(source, 2, 1), source);
});

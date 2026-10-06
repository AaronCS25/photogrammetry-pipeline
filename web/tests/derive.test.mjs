import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { derive, indexManzanas, contains } from '../src/lib/derive.mjs';
import { openStore } from '../src/lib/store.mjs';

const square = (x, y, d = 1) => ({ type: 'Polygon', coordinates: [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]] });
const gis = { features: [
  { geometry: square(0, 0), properties: { id: 'MZ-BAR-AAAAAA0000', scene: 'mz_aaaaaa' } },
  { geometry: square(5, 5), properties: { id: 'MZ-BAR-BBBBBB0000', scene: 'mz_bbbbbb' } },
] };
const index = indexManzanas(gis);

test('point in polygon', () => {
  assert.ok(contains(square(0, 0), [.5, .5]));
  assert.ok(!contains(square(0, 0), [1.5, .5]));
  assert.equal(index.near(.5, 1.0002), 'MZ-BAR-AAAAAA0000'); // ~22 m fuera del borde
  assert.equal(index.near(.5, 1.001), null);                 // ~111 m
});

test('vínculo escena→manzana por prioridad', () => {
  const snapshot = { jobs: [], scenes: [
    { name: 'mz_aaaaaa_v2', sources: [] },
    { name: 'sv_manzana_a', sources: [], identification: { code: 'MZ-BAR-BBBBBB0000' } },
    { name: 'hornero', sources: [], experiments: [{ name: 'e', stages: {}, origin: { lat: 5.5, lon: 5.5 } }] },
    { name: 'gps_cero', sources: [], experiments: [{ name: 'e', stages: {}, origin: { lat: 0, lon: 0 } }] },
    { name: 'libre', sources: [] },
  ] };
  const by = Object.fromEntries(derive(snapshot, index).scenes.map(s => [s.name, [s.manzana, s.via]]));
  assert.deepEqual(by.mz_aaaaaa_v2, ['MZ-BAR-AAAAAA0000', 'nombre']);
  assert.deepEqual(by.sv_manzana_a, ['MZ-BAR-BBBBBB0000', 'export']);
  assert.deepEqual(by.hornero, ['MZ-BAR-BBBBBB0000', 'georef']);
  assert.deepEqual(by.gps_cero, [null, null]);
  assert.deepEqual(by.libre, [null, null]);
  // manual gana, y null manual = "sin manzana" explícito
  const manual = derive(snapshot, index, { libre: 'MZ-BAR-AAAAAA0000', sv_manzana_a: null }).scenes;
  assert.equal(manual.find(s => s.name === 'libre').manzana, 'MZ-BAR-AAAAAA0000');
  assert.equal(manual.find(s => s.name === 'sv_manzana_a').manzana, null);
});

test('estados de experimento y de manzana', () => {
  const exp = (name, extra) => ({ name, stages: {}, jobs: [], ...extra });
  const snapshot = {
    jobs: [
      { id: '30', state: 'PENDING', scene: 'mz_aaaaaa', experiment: 'nuevo_v1', active: true },
      { id: '20', state: 'OUT_OF_MEMORY', scene: 'mz_aaaaaa', experiment: 'roto_v1' },
      { id: '10', state: 'COMPLETED' },
    ],
    scenes: [
      { name: 'mz_aaaaaa', sources: [{ name: 'phone' }], experiments: [
        exp('listo_v1', { jobs: ['10'], textured: true, stages: { metrics: {} } }),
        exp('roto_v1', { jobs: ['20'] }),
      ] },
      { name: 'mz_bbbbbb', sources: [{ name: 'drone' }] },
    ],
  };
  const d = derive(snapshot, index);
  const a = d.scenes.find(s => s.name === 'mz_aaaaaa');
  assert.deepEqual(a.experiments.map(e => [e.name, e.status]), [['nuevo_v1', 'queued'], ['listo_v1', 'done'], ['roto_v1', 'failed']]);
  assert.equal(a.experiments[0].placeholder, true);
  assert.equal(d.manzanas['MZ-BAR-AAAAAA0000'].status, 'queued');
  assert.equal(d.manzanas['MZ-BAR-BBBBBB0000'].status, 'data');
});

test('store: vínculos, nombres e instantáneas', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'studio-'));
  const store = openStore(dir);
  try {
    store.link('a', 'MZ-1'); store.link('b', null); store.rename('MZ-1', 'Bajada de Baños');
    assert.deepEqual(store.links(), { a: 'MZ-1', b: null });
    store.unlink('a');
    assert.deepEqual(store.links(), { b: null });
    assert.deepEqual(store.names(), { 'MZ-1': 'Bajada de Baños' });
    for (let i = 0; i < 7; i++) store.snapshot({ scenes: [], n: i });
    assert.equal(store.snapshot().n, 6);
  } finally { store.close(); rmSync(dir, { recursive: true, force: true }); }
});

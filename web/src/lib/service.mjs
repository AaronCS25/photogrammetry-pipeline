import { readFileSync } from 'node:fs';
import path from 'node:path';
import { openStore } from './store.mjs';
import { remote } from './connector.mjs';
import { derive, indexManzanas } from './derive.mjs';

export const store = openStore();
const manzanas = indexManzanas(JSON.parse(readFileSync(path.resolve('public/gis/manzanas.geojson'), 'utf8')));
const MIN_INTERVAL = 30_000; // como mucho un listado cada 30 s, aunque se pulse "Actualizar" repetidamente

// Operaciones SSH serializadas: nunca dos conexiones simultáneas a Khipu.
let queue = Promise.resolve(), busy = 0;
function serial(task) { busy++; const result = queue.then(task); queue = result.catch(() => {}).finally(() => busy--); return result; }

const SCENE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$/;
function scene(value) { if (!SCENE.test(String(value)) || String(value).includes('..')) throw Error('Escena no válida.'); return String(value); }
function manzana(value) { if (!manzanas.byId.has(value)) throw Error('Manzana desconocida.'); return value; }

export function state() {
  const snapshot = store.snapshot();
  return {
    ...derive(snapshot, manzanas, store.links()),
    root: snapshot?.root, lastSync: snapshot?.at || null, connection: store.setting('connection'),
    names: store.names(), links: store.links(), auto: store.setting('auto') !== false, busy,
  };
}

async function guarded(fn) {
  try { const result = await fn(); store.setting('connection', { ok: true, at: Date.now() }); return result; }
  catch (e) { store.setting('connection', { ok: false, error: e.message, at: Date.now() }); throw e; }
}

export function health() {
  return serial(() => guarded(async () => {
    const result = await remote({ op: 'health' });
    if (!result.ready) throw Error(`Conectado, pero no se encontró el pipeline en ${result.root} (falta slurm/submit.sh).`);
    return result;
  }));
}

export function refresh({ force = false } = {}) {
  return serial(() => guarded(async () => {
    const last = store.snapshot()?.at || 0;
    if (!force && Date.now() - last < MIN_INTERVAL) return { skipped: true };
    store.snapshot(await remote({ op: 'list', days: 21 }));
    return { skipped: false };
  }));
}

export function metrics(sceneName, experiment) {
  return serial(() => guarded(() => remote({ op: 'metrics', scene: scene(sceneName), experiment: scene(experiment) })));
}

export function log(job, filtered) {
  if (!/^[0-9]{1,12}$/.test(String(job))) throw Error('Job no válido.');
  return serial(() => guarded(() => remote({ op: 'log', job: String(job), filtered: Boolean(filtered), lines: 300 })));
}

export function link(sceneName, manzanaId) {
  sceneName = scene(sceneName);
  if (manzanaId === undefined) store.unlink(sceneName);
  else store.link(sceneName, manzanaId === null ? null : manzana(manzanaId));
}

export function rename(id, name) {
  name = String(name || '').trim();
  if (name.length > 80 || /[\x00-\x1f]/.test(name)) throw Error('Nombre no válido (máx. 80 caracteres).');
  store.rename(manzana(id), name);
}

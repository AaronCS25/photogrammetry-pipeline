import { readFileSync, statSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
import { parse as parseYaml } from 'yaml';
import { openStore } from './store.mjs';
import { remote, execute } from './connector.mjs';
import { derive, indexManzanas } from './derive.mjs';
import { scanFolder, KINDS } from './media.mjs';
import { uploadTar } from './upload.mjs';
import { buildConfig, toYaml, parseSbatch, sbatchFromForm, gresType, STAGES, PRESETS } from './presets.mjs';
import { storePreviews, cachedPreviews, fetchFiles, partSize, openFolder, downloadDir, compareRows } from './results.mjs';

export const store = openStore();
const manzanas = indexManzanas(JSON.parse(readFileSync(path.resolve('public/gis/manzanas.geojson'), 'utf8')));
const MIN_INTERVAL = 30_000; // como mucho un listado cada 30 s, aunque se pulse "Actualizar" repetidamente

// Operaciones SSH serializadas: nunca dos conexiones simultáneas a Khipu para consultas/envíos.
// Las subidas van por un carril aparte (una a la vez) para no bloquear el seguimiento durante minutos.
let queue = Promise.resolve(), busy = 0;
function serial(task) { busy++; const result = queue.then(task); queue = result.catch(() => {}).finally(() => busy--); return result; }
let uploadQueue = Promise.resolve();
const uploads = new Map(); // datasetId -> {controller, progress}
let downloadQueue = Promise.resolve();
const downloads = new Map(); // `${escena}__${exp}` -> {controller, current}

const SCENE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$/;
const NAME = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/; // escenas y experimentos nuevos
function scene(value) { if (!SCENE.test(String(value)) || String(value).includes('..')) throw Error('Escena no válida.'); return String(value); }
function name(value, what = 'Nombre') { if (!NAME.test(String(value))) throw Error(`${what} no válido: usa letras, números, _ o - (máx. 64).`); return String(value); }
function manzana(value) { if (!manzanas.byId.has(value)) throw Error('Manzana desconocida.'); return value; }
const sha256 = text => createHash('sha256').update(text).digest('hex');
const newId = () => randomUUID().replaceAll('-', '');

// Al reiniciar: una subida en curso queda interrumpida (reanudable); un envío sin respuesta, incierto.
for (const d of store.datasets.all()) {
  let changed = false;
  for (const s of d.sources) if (s.status === 'uploading') { s.status = 'interrupted'; changed = true; }
  if (changed) store.datasets.put(d);
}
for (const l of store.launches.all()) if (l.status === 'submitting') store.launches.put({ ...l, status: 'uncertain', error: 'La app se reinició durante el envío. Usa «Comprobar envío».' });

function remoteScenes() { return new Set((store.snapshot()?.scenes || []).map(s => s.name)); }
function activeMemG(jobs) {
  return jobs.filter(j => j.active).reduce((sum, j) => {
    const m = String(j.reqmem || '').match(/^(\d+(?:\.\d+)?)([KMGT]?)/);
    return sum + (m ? Number(m[1]) * ({ K: 1 / 1048576, M: 1 / 1024, G: 1, T: 1024 }[m[2]] ?? 1 / 1024) : 64);
  }, 0);
}

export function state() {
  const snapshot = store.snapshot();
  const launches = store.launches.all();
  const derived = derive(snapshot, manzanas, store.links(), launches);
  const datasets = store.datasets.all().map(d => ({ ...d, progress: uploads.get(d.id)?.progress || null }));
  return {
    ...derived,
    datasets, experimentsLocal: store.experiments.all(), launches, events: store.events(15),
    activeMemG: activeMemG(derived.jobs),
    root: snapshot?.root, lastSync: snapshot?.at || null, connection: store.setting('connection'),
    names: store.names(), links: store.links(), auto: store.setting('auto') !== false, busy,
    uploading: [...uploads.keys()],
    downloads: store.downloads.all().map(d => ({ ...d, progress: downloadProgress(d) })),
    downloading: [...downloads.keys()],
  };
}

async function guarded(fn) {
  try { const result = await fn(); store.setting('connection', { ok: true, at: Date.now() }); return result; }
  catch (e) { if (/ssh|conexi|Connection|timed out|tardó/i.test(e.message)) store.setting('connection', { ok: false, error: e.message, at: Date.now() }); throw e; }
}
const call = (request, timeout) => serial(() => guarded(() => remote(request, timeout)));

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

// Versión de un experimento según el último listado: cambia si se rehacen etapas o se reescribe metrics.json.
const metricsCache = new Map();
function expVersion(sceneName, experiment) {
  const e = store.snapshot()?.scenes?.find(s => s.name === sceneName)?.experiments?.find(x => x.name === experiment);
  return e ? JSON.stringify([e.mtime, e.metrics?.generated_at, Object.values(e.stages || {}).map(st => st.at)]) : null;
}
export async function metrics(sceneName, experiment) {
  const key = `${scene(sceneName)}/${scene(experiment)}`, version = expVersion(sceneName, experiment);
  const hit = metricsCache.get(key);
  if (hit && version && hit.version === version) return hit.data;
  const data = await call({ op: 'metrics', scene: sceneName, experiment });
  metricsCache.set(key, { version, data });
  return data;
}

export function log(job, filtered) {
  if (!/^[0-9]{1,12}$/.test(String(job))) throw Error('Job no válido.');
  return call({ op: 'log', job: String(job), filtered: Boolean(filtered), lines: 300 });
}

export function link(sceneName, manzanaId) {
  sceneName = scene(sceneName);
  if (manzanaId === undefined) store.unlink(sceneName);
  else store.link(sceneName, manzanaId === null ? null : manzana(manzanaId));
}

export function rename(id, value) {
  value = String(value || '').trim();
  if (value.length > 80 || /[\x00-\x1f]/.test(value)) throw Error('Nombre no válido (máx. 80 caracteres).');
  store.rename(manzana(id), value);
}

// ------------------------------------------------------------------ capturas
export function scan(folder, kind) {
  if (!KINDS[kind]) throw Error('Tipo de fuente desconocido.');
  const result = scanFolder(String(folder || '').trim(), kind);
  const { files, ...summary } = result; // la lista completa no viaja al navegador
  return { ...summary, groups: result.groups.map(g => ({ suffix: g.suffix, dims: g.dims, count: g.files.length })) };
}

/** Diálogo nativo de Windows para elegir carpeta (el servidor corre en el escritorio del usuario). */
export async function pickFolder() {
  if (process.platform !== 'win32') throw Error('Solo disponible en Windows: escribe la ruta.');
  const script = "Add-Type -AssemblyName System.Windows.Forms; [Console]::OutputEncoding=[Text.Encoding]::UTF8; " +
    "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true}; $d=New-Object System.Windows.Forms.FolderBrowserDialog; " +
    "$d.Description='Carpeta de la captura'; $d.ShowNewFolderButton=$false; if($d.ShowDialog($o) -eq 'OK'){$d.SelectedPath}";
  const out = await execute('powershell.exe', ['-NoProfile', '-STA', '-Command', script], '', 300000);
  return { folder: out.trim() || null };
}

function sourceDir(src, suffix = '') { return src.kind === 'streetview_export' ? '' : `${src.name}${suffix}`; }

export function createDataset({ manzana: mz, scene: sceneName, notes, sources }) {
  sceneName = name(sceneName, 'Nombre de escena');
  if (mz) manzana(mz);
  if (store.datasets.all().some(d => d.scene === sceneName)) throw Error(`Ya hay una captura local llamada ${sceneName}.`);
  if (remoteScenes().has(sceneName)) throw Error(`La escena ${sceneName} ya existe en Khipu. Elige otro nombre (p. ej. ${sceneName}_b).`);
  if (!Array.isArray(sources) || !sources.length) throw Error('Añade al menos una fuente.');
  const list = sources.map(s => {
    if (!KINDS[s.kind]) throw Error('Tipo de fuente desconocido.');
    const scanResult = scanFolder(String(s.folder || '').trim(), s.kind);
    if (scanResult.errors.length) throw Error(`${s.folder}: ${scanResult.errors.join(' ')}`);
    return { id: newId(), kind: s.kind, name: s.kind === 'streetview_export' ? '' : name(s.name, 'Nombre de fuente'), folder: scanResult.folder,
      count: scanResult.count, bytes: scanResult.bytes, summary: scanResult.summary, warnings: scanResult.warnings,
      groups: scanResult.groups.map(g => ({ suffix: g.suffix, dims: g.dims, count: g.files.length })), status: 'pending' };
  });
  const dirs = list.flatMap(s => s.groups.map(g => sourceDir(s, g.suffix)));
  if (new Set(dirs).size !== dirs.length) throw Error('Dos fuentes van a la misma carpeta: usa nombres distintos.');
  if (list.some(s => s.kind === 'streetview_export') && list.length > 1) throw Error('Un export de Street View se sube solo, como escena propia.');
  const d = store.datasets.put({ id: newId(), scene: sceneName, manzana: mz || null, notes: String(notes || '').slice(0, 500),
    created: new Date().toISOString(), sources: list });
  if (mz) store.link(sceneName, mz);
  store.event('dataset', d.id, `Captura ${sceneName} creada (${list.length} fuente(s), ${list.reduce((a, s) => a + s.count, 0)} archivos).`);
  return d;
}

export function deleteDataset(id) {
  const d = store.datasets.get(id);
  if (!d) throw Error('Captura no encontrada.');
  if (uploads.has(id)) throw Error('Cancela la subida antes de quitarla.');
  store.datasets.remove(id);
  store.event('dataset', id, `Captura ${d.scene} quitada de la lista local (no se borra nada en Khipu).`);
}

/** Archivos locales de una fuente con su nombre dentro de la escena (`<fuente>[_dims]/<archivo>`). */
function plan(src) {
  const scanResult = scanFolder(src.folder, src.kind);
  return scanResult.groups.flatMap(g => g.files.map(rel => {
    const full = path.join(src.folder, ...rel.split('/')), st = statSync(full);
    const dir = sourceDir(src, g.suffix);
    return { full, name: dir ? `${dir}/${rel}` : rel, size: st.size, mtime: st.mtimeMs };
  }));
}

export function upload(id) {
  const d = store.datasets.get(id);
  if (!d) throw Error('Captura no encontrada.');
  if (uploads.has(id)) throw Error('La subida ya está en curso.');
  const controller = new AbortController();
  const entry = { controller, progress: { phase: 'en cola', sent: 0, total: 0, files: 0, done: 0, current: null, startedAt: Date.now() } };
  uploads.set(id, entry);
  const save = (sourceId, patch) => {
    const doc = store.datasets.get(id);
    doc.sources = doc.sources.map(s => s.id === sourceId ? { ...s, ...patch } : s);
    store.datasets.put(doc);
  };
  uploadQueue = uploadQueue.then(async () => {
    try {
      let root = store.snapshot()?.root;
      if (!root) root = (await health()).root;
      entry.progress.phase = 'comparando con Khipu';
      const existing = (await call({ op: 'ls', scene: d.scene })).files;
      const sources = d.sources.map(s => ({ s, files: plan(s) }));
      const pending = sources.map(({ s, files }) => ({ s, files, todo: files.filter(f => existing[f.name] !== f.size) }));
      Object.assign(entry.progress, { total: pending.reduce((a, p) => a + p.todo.reduce((b, f) => b + f.size, 0), 0),
        files: pending.reduce((a, p) => a + p.todo.length, 0), skipped: pending.reduce((a, p) => a + p.files.length - p.todo.length, 0) });
      let base = 0;
      for (const { s, todo } of pending) {
        if (!todo.length) { save(s.id, { status: 'uploaded' }); continue; }
        save(s.id, { status: 'uploading', error: null });
        entry.progress.phase = `subiendo ${s.name || 'export'}`;
        const offset = base;
        await uploadTar({ root, scene: d.scene, files: todo, signal: controller.signal,
          progress: (sent, file) => { entry.progress.sent = offset + sent; if (entry.progress.current !== file.name) { entry.progress.current = file.name; entry.progress.done++; } } });
        base += todo.reduce((a, f) => a + f.size, 0);
        save(s.id, { status: 'uploaded', uploadedAt: new Date().toISOString() });
      }
      entry.progress.phase = 'verificando';
      const after = (await call({ op: 'ls', scene: d.scene })).files;
      for (const { s, files } of sources) {
        const missing = files.filter(f => after[f.name] !== f.size);
        save(s.id, missing.length ? { status: 'error', error: `${missing.length} archivo(s) no coinciden en Khipu (p. ej. ${missing[0].name}). Reanuda la subida.` }
          : { status: 'verified', verifiedAt: new Date().toISOString(), remoteCount: files.length });
      }
      store.event('upload', id, `Subida de ${d.scene}: ${entry.progress.files} archivo(s) enviados, ${entry.progress.skipped} ya estaban.`);
    } catch (e) {
      const doc = store.datasets.get(id);
      if (doc) { doc.sources = doc.sources.map(s => s.status === 'uploading' || s.status === 'pending' ? { ...s, status: controller.signal.aborted ? 'interrupted' : 'error', error: e.message } : s); store.datasets.put(doc); }
      store.event('upload', id, `Subida de ${d.scene} interrumpida: ${e.message}`);
    } finally {
      uploads.delete(id);
    }
  });
  return { ok: true };
}

export function cancelUpload(id) {
  const u = uploads.get(id);
  if (!u) throw Error('No hay subida en curso.');
  u.controller.abort();
  return { ok: true };
}

// ------------------------------------------------------------------ experimentos
function existingExperimentNames(sceneName) {
  const remoteNames = (store.snapshot()?.scenes || []).find(s => s.name === sceneName)?.experiments?.map(e => e.name) || [];
  return new Set([...remoteNames, ...store.experiments.all().filter(e => e.scene === sceneName).map(e => e.name)]);
}

/** Guarda un borrador: modo simple (formulario → YAML) o avanzado (YAML editado a mano). */
export function saveExperiment(input) {
  const sceneName = name(input.scene, 'Escena');
  const previous = input.id ? store.experiments.get(input.id) : null;
  if (input.id && !previous) throw Error('Experimento no encontrado.');
  let yamlText, form = null, expName;
  if (input.mode === 'advanced') {
    yamlText = String(input.yaml || '');
    if (yamlText.length > 60000) throw Error('YAML demasiado grande.');
    let cfg;
    try { cfg = parseYaml(yamlText); } catch (e) { throw Error(`YAML no válido: ${e.message.split('\n')[0]}`); }
    if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) throw Error('El YAML debe ser un mapeo.');
    expName = name(cfg.experiment?.name, 'experiment.name');
    if (cfg.scene && cfg.scene !== sceneName) throw Error('El YAML fija otra escena (clave scene).');
    if (cfg.paths) throw Error('No cambies paths: las salidas deben quedar en outputs/.');
    form = input.form || previous?.form || null;
  } else {
    form = input.form;
    if (!PRESETS[form?.preset]) throw Error('Elige un preset.');
    expName = name(form.name, 'Nombre del experimento');
    yamlText = toYaml(buildConfig(form), `Generado por photogrammetry-studio · preset ${form.preset} (base: configs/experiments/${PRESETS[form.preset].source})\nEscena: ${sceneName}`);
  }
  if ((!previous || previous.name !== expName) && existingExperimentNames(sceneName).has(expName)) throw Error(`Ya existe ${expName} en ${sceneName}. Cambia el nombre.`);
  if (previous?.launched) throw Error('Este experimento ya se lanzó; clónalo para cambiarlo.');
  const sbatch = parseSbatch(input.sbatch ?? (form ? sbatchFromForm(form) : '')).join(' ');
  const cfg = parseYaml(yamlText);
  const usesSam3 = cfg.masking?.enabled && JSON.stringify(cfg.masking).includes('sam3');
  if (usesSam3 && !['rtxa6000', 'a100'].includes(gresType(sbatch))) throw Error('SAM 3 exige A6000 o A100: usa --gres=shard:rtxa6000:N o shard:a100:N.');
  // Reutilizar etapas de otro experimento: se copia su carpeta en Khipu justo antes de lanzar.
  const clone = input.clone?.from && STAGES.slice(1).includes(input.clone.fromStage) ? { from: scene(input.clone.from), fromStage: input.clone.fromStage } : null;
  const doc = {
    ...(previous || { id: newId(), created: new Date().toISOString() }),
    scene: sceneName, manzana: input.manzana || previous?.manzana || null, name: expName, mode: input.mode === 'advanced' ? 'advanced' : 'simple',
    form, yaml: yamlText, sha256: sha256(yamlText), sbatch, clone,
  };
  if (previous && previous.sha256 !== doc.sha256) doc.validation = null; // cambió el YAML: hay que validar de nuevo
  return store.experiments.put(doc);
}

export function deleteExperiment(id) {
  const e = store.experiments.get(id);
  if (!e) throw Error('Experimento no encontrado.');
  if (e.launched) throw Error('Ya se lanzó: queda en el historial.');
  store.experiments.remove(id);
}

export async function validateExperiment(id) {
  const e = store.experiments.get(id);
  if (!e) throw Error('Experimento no encontrado.');
  const result = await call({ op: 'validate', scene: e.scene, experiment: e.name, yaml: Buffer.from(e.yaml).toString('base64') }, 300000);
  const validation = { ok: result.ok && result.sha256 === e.sha256, output: result.output, sha256: result.sha256, config: result.config, at: Date.now() };
  if (result.ok && result.sha256 !== e.sha256) validation.output += '\n(El archivo en Khipu no coincide con el borrador: vuelve a validar.)';
  store.experiments.put({ ...store.experiments.get(id), validation });
  store.event('validate', id, `${e.scene}/${e.name}: ${validation.ok ? 'configuración válida' : 'validación con errores'}.`);
  return validation;
}

async function send(launch) {
  try {
    const receipt = await call({ op: 'submit', id: launch.id, scene: launch.scene, experiment: launch.experiment, config: launch.config,
      sha256: launch.sha256, sbatch: launch.sbatch.split(' ').filter(Boolean), args: launch.args, clone: launch.clone }, launch.clone ? 900000 : 180000);
    const status = receipt.job ? 'submitted' : receipt.status;
    const doc = store.launches.put({ ...store.launches.get(launch.id), status, job: receipt.job || null, error: status === 'uncertain' ? 'No se encontró el job de este envío. Revisa squeue/slurm-logs antes de lanzar otro.' : null, reconciled: receipt.reconciled || null });
    store.event('submit', launch.id, receipt.job ? `${launch.scene}/${launch.experiment}: job ${receipt.job} enviado${launch.args.length ? ` (${launch.args.join(' ')})` : ''}.` : `${launch.scene}/${launch.experiment}: envío incierto.`);
    refresh({ force: true }).catch(() => {});
    return doc;
  } catch (e) {
    // Error de conexión: el envío pudo llegar. Queda incierto y se puede comprobar con el mismo id (idempotente).
    const uncertain = /tardó|conexi|ssh|Connection|respuesta válida/i.test(e.message);
    store.launches.put({ ...store.launches.get(launch.id), status: uncertain ? 'uncertain' : 'error', error: e.message });
    store.event('submit', launch.id, `${launch.scene}/${launch.experiment}: ${e.message}`);
    throw e;
  }
}

export async function launchExperiment(id) {
  const e = store.experiments.get(id);
  if (!e) throw Error('Experimento no encontrado.');
  if (!e.validation?.ok || e.validation.sha256 !== e.sha256) throw Error('Valida la configuración antes de lanzar.');
  if (store.launches.all().some(l => l.experimentId === id && ['submitting', 'uncertain'].includes(l.status))) throw Error('Hay un envío pendiente de confirmar para este experimento.');
  const launch = store.launches.put({ id: newId(), experimentId: id, scene: e.scene, experiment: e.name, config: e.validation.config, sha256: e.sha256,
    sbatch: e.sbatch, args: [], clone: e.clone, kind: e.clone ? 'clone' : 'new', status: 'submitting', at: Date.now() });
  store.experiments.put({ ...e, launched: true });
  return send(launch);
}

/** Reintenta un envío incierto con el MISMO id: el bridge devuelve el recibo o busca el job, nunca duplica. */
export function checkLaunch(id) {
  const l = store.launches.get(id);
  if (!l) throw Error('Envío no encontrado.');
  return send(l);
}

/** Relanza un experimento existente: reanudar (etapas hechas se omiten) o repetir desde una etapa (--force). */
export async function relaunch({ scene: sceneName, experiment, fromStage, sbatch }) {
  sceneName = name(sceneName, 'Escena'); experiment = scene(experiment);
  const snap = store.snapshot();
  const exp = snap?.scenes?.find(s => s.name === sceneName)?.experiments?.find(e => e.name === experiment);
  if (!exp) throw Error('Experimento no encontrado en el último listado. Actualiza.');
  const lastJob = (snap.jobs || []).filter(j => (exp.jobs || []).includes(j.id) && j.config).sort((a, b) => b.id - a.id)[0];
  const config = exp.uiConfig ? `datasets/raw/${sceneName}/_ui/${experiment}.yaml` : lastJob?.config;
  if (!config) throw Error('No se sabe con qué YAML se lanzó (no hay log con cabecera). Clónalo como nuevo experimento.');
  if (fromStage && !STAGES.includes(fromStage)) throw Error('Etapa no válida.');
  const tokens = parseSbatch(sbatch).join(' ');
  const validation = await call({ op: 'validate', scene: sceneName, config }, 300000);
  if (!validation.ok) return { validation };
  const launch = store.launches.put({ id: newId(), experimentId: null, scene: sceneName, experiment, config, sha256: validation.sha256, sbatch: tokens,
    args: fromStage ? ['--from-stage', fromStage, '--force'] : [], clone: null, kind: fromStage ? 'from-stage' : 'resume', status: 'submitting', at: Date.now() });
  return { validation, launch: await send(launch) };
}

/** Borrador nuevo a partir de un experimento existente (formulario si lo creó la app; si no, su YAML). */
export async function cloneExperiment({ scene: sceneName, experiment }) {
  sceneName = name(sceneName, 'Escena');
  const local = store.experiments.all().find(e => e.scene === sceneName && e.name === experiment);
  if (local) return { mode: local.mode, form: local.form, yaml: local.yaml, sbatch: local.sbatch };
  const r = await call({ op: 'read_config', scene: sceneName, experiment: scene(experiment) });
  return { mode: 'advanced', yaml: r.text, source: r.path, form: null };
}

export function cancelJob(job) {
  if (!/^[0-9]{1,12}$/.test(String(job))) throw Error('Job no válido.');
  return call({ op: 'cancel', job: String(job) }).then(r => { store.event('cancel', String(job), `scancel ${job}.`); refresh({ force: true }).catch(() => {}); return r; });
}

// ------------------------------------------------------------------ resultados (fase 3)
/** Vistas previas de máscaras: caché local salvo que el experimento haya cambiado o se pidan de nuevo. */
export async function previews(sceneName, experiment, force = false) {
  sceneName = scene(sceneName); experiment = scene(experiment);
  const cached = cachedPreviews(sceneName, experiment), version = expVersion(sceneName, experiment);
  if (cached && !force && version && cached.version === version) return cached;
  const result = await call({ op: 'previews', scene: sceneName, experiment, force, max: 12 }, 360000);
  return storePreviews(sceneName, experiment, { ...result, version });
}
export function cachedPreviewIndex(sceneName, experiment) { return cachedPreviews(scene(sceneName), scene(experiment)); }

function downloadProgress(d) {
  const live = downloads.get(d.id);
  if (!live) return null;
  const done = d.files.filter(f => f.done).reduce((a, f) => a + f.size, 0);
  return { current: live.current, sent: done + (live.current ? partSize(d.dir, live.current) : 0), total: d.bytes };
}

/** Descarga la malla texturizada (+ métricas) a ~/Downloads/barranco_experiments/<escena>__<exp>/. */
export function downloadMesh(sceneName, experiment) {
  sceneName = scene(sceneName); experiment = scene(experiment);
  const key = `${sceneName}__${experiment}`;
  if (downloads.has(key)) throw Error('La descarga ya está en curso.');
  const controller = new AbortController(), live = { controller, current: null };
  downloads.set(key, live);
  downloadQueue = downloadQueue.then(async () => {
    const dir = downloadDir(sceneName, experiment);
    let doc = { id: key, scene: sceneName, experiment, dir, status: 'listando', at: Date.now(), files: [], bytes: 0, error: null };
    store.downloads.put(doc);
    try {
      const root = store.snapshot()?.root || (await health()).root;
      const list = await call({ op: 'mesh_files', scene: sceneName, experiment });
      doc = store.downloads.put({ ...doc, status: 'descargando', files: list.files.map(f => ({ ...f, done: false })), bytes: list.bytes });
      await fetchFiles({ root, scene: sceneName, experiment, files: list.files, dir, signal: controller.signal,
        onFile: name => {
          live.current = name;
          const cur = store.downloads.get(key);
          // marca como hechos los anteriores al archivo en curso
          const idx = cur.files.findIndex(f => f.name === name);
          store.downloads.put({ ...cur, files: cur.files.map((f, i) => i < idx ? { ...f, done: true } : f) });
        } });
      const cur = store.downloads.get(key);
      store.downloads.put({ ...cur, status: 'lista', files: cur.files.map(f => ({ ...f, done: true })), finishedAt: Date.now() });
      store.event('download', key, `Malla de ${sceneName}/${experiment} descargada en ${dir}.`);
    } catch (e) {
      store.downloads.put({ ...store.downloads.get(key), status: 'error', error: e.message });
      store.event('download', key, `Descarga de ${sceneName}/${experiment}: ${e.message}`);
    } finally { downloads.delete(key); }
  });
  return { ok: true };
}

export function openDownload(key) {
  const d = store.downloads.get(String(key));
  if (!d) throw Error('Descarga no encontrada.');
  openFolder(d.dir);
  return { ok: true };
}

/** Tabla comparativa de dos experimentos (pueden ser de escenas distintas de la misma manzana). */
export async function compare(a, b) {
  const [ma, mb] = [await metrics(a.scene, a.experiment), await metrics(b.scene, b.experiment)];
  if (!ma.metrics && !mb.metrics) throw Error('Ninguno de los dos tiene metrics.json todavía.');
  return { a, b, rows: compareRows(ma.metrics || {}, mb.metrics || {}) };
}


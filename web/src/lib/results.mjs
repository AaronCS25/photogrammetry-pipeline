// Fase 3: vistas previas de máscaras (caché local), descarga de mallas y comparación de experimentos.
import { mkdirSync, writeFileSync, readFileSync, existsSync, statSync, renameSync, unlinkSync, createReadStream } from 'node:fs';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { execute, options } from './connector.mjs';
import { dataDir } from './store.mjs';

const ID = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$/;
export function id(value, what = 'Identificador') {
  if (!ID.test(String(value)) || String(value).includes('..')) throw Error(`${what} no válido.`);
  return String(value);
}
export const downloadsRoot = path.resolve(process.env.STUDIO_DOWNLOADS || path.join(os.homedir(), 'Downloads', 'barranco_experiments'));
export const downloadDir = (scene, experiment) => path.join(downloadsRoot, `${id(scene)}__${id(experiment)}`);
const previewsRoot = () => path.join(dataDir, 'previews');

// ------------------------------------------------------------------ vistas previas
/** Guarda las imágenes devueltas por el bridge y el índice; devuelve el índice con URLs locales. */
export function storePreviews(scene, experiment, result) {
  const dir = path.join(previewsRoot(), id(scene), id(experiment));
  mkdirSync(dir, { recursive: true });
  for (const [file, b64] of Object.entries(result.files || {})) {
    if (!/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+\.jpg$/.test(file) || file.includes('..')) continue;
    mkdirSync(path.join(dir, path.dirname(file)), { recursive: true });
    writeFileSync(path.join(dir, file), Buffer.from(b64, 'base64'));
  }
  const index = { ...result.index, fetchedAt: Date.now(), reused: result.reused, version: result.version };
  writeFileSync(path.join(dir, 'index.json'), JSON.stringify(index));
  return withUrls(scene, experiment, index);
}
function withUrls(scene, experiment, index) {
  return { ...index, items: (index.items || []).map(i => ({ ...i, url: `/api/preview/${encodeURIComponent(scene)}/${encodeURIComponent(experiment)}/${i.file}` })) };
}
export function cachedPreviews(scene, experiment) {
  const file = path.join(previewsRoot(), id(scene), id(experiment), 'index.json');
  return existsSync(file) ? withUrls(scene, experiment, JSON.parse(readFileSync(file, 'utf8'))) : null;
}
export function previewPath(scene, experiment, source, file) {
  if (!/^[a-zA-Z0-9_.-]+$/.test(source) || !/^[a-zA-Z0-9_.-]+\.jpg$/.test(file) || source.includes('..') || file.includes('..')) throw Error('Archivo no válido.');
  return path.join(previewsRoot(), id(scene), id(experiment), source, file);
}

// ------------------------------------------------------------------ descarga de mallas
const REMOTE = /^\/[A-Za-z0-9._\/-]+$/;
function sha256File(file) {
  return new Promise((resolve, reject) => {
    const h = createHash('sha256');
    createReadStream(file).on('data', d => h.update(d)).on('end', () => resolve(h.digest('hex'))).on('error', reject);
  });
}

/**
 * Copia cada archivo con scp a <dir>/<nombre>.part, verifica SHA-256 y renombra. Los archivos ya
 * descargados con el mismo hash se omiten (re-descargar es barato y seguro).
 * `onFile(name)` avisa del archivo en curso (el progreso se mide por el tamaño del .part).
 */
export async function fetchFiles({ root, scene, experiment, files, dir, onFile = () => {}, signal }) {
  const prefix = `${root}/outputs/${id(scene)}/${id(experiment)}/`;
  mkdirSync(dir, { recursive: true });
  for (const f of files) {
    if (!REMOTE.test(f.path) || f.path.includes('..') || !f.path.startsWith(prefix)) throw Error(`Ruta remota inesperada: ${f.path}`);
    if (!/^[a-zA-Z0-9_.-]+$/.test(f.name) || !/^[a-f0-9]{64}$/.test(f.sha256)) throw Error('Archivo remoto no válido.');
    const dest = path.join(dir, f.name);
    if (existsSync(dest) && statSync(dest).size === f.size && await sha256File(dest) === f.sha256) continue;
    if (signal?.aborted) throw Error('Descarga cancelada.');
    onFile(f.name);
    const part = dest + '.part';
    await execute('scp', ['-q', ...options, `${process.env.KHIPU_HOST || 'khipu'}:${f.path}`, part], '', 1800000);
    if (await sha256File(part) !== f.sha256) { unlinkSync(part); throw Error(`${f.name} no coincide con su checksum. Vuelve a descargar.`); }
    renameSync(part, dest);
  }
}
export function partSize(dir, name) {
  try { return statSync(path.join(dir, name + '.part')).size; } catch { return 0; }
}

/** Abre una carpeta de descargas en el Explorador (solo dentro de la raíz de descargas). */
export function openFolder(dir) {
  const resolved = path.resolve(dir);
  if (path.relative(downloadsRoot, resolved).startsWith('..') || !existsSync(resolved)) throw Error('Carpeta no disponible.');
  const program = process.platform === 'win32' ? 'explorer.exe' : process.platform === 'darwin' ? 'open' : 'xdg-open';
  spawn(program, [resolved], { detached: true, stdio: 'ignore', windowsHide: false }).unref();
}

// ------------------------------------------------------------------ comparación
const get = (o, p) => p.split('.').reduce((a, k) => a?.[k], o);
/** Filas de la tabla comparativa: [etiqueta, valor A, valor B, mejor ('high'|'low'|null)]. */
export function compareRows(a, b) {
  const stages = ['frames', 'telemetry', 'masks', 'sfm', 'georef', 'undistort', 'dense', 'mesh', 'texture', 'metrics'];
  const masked = m => { const r = m?.masking?.mean_masked_ratio; return r ? Object.entries(r).map(([k, v]) => `${k} ${(100 * v).toFixed(1)} %`).join(', ') : null; };
  const rows = [
    ['Notas', 'notes', null], ['Imágenes', 'frames_total', null], ['Registradas', 'sparse.registered_images', 'high'],
    ['% registradas', m => m?.sparse?.registration_ratio != null ? +(100 * m.sparse.registration_ratio).toFixed(1) : null, 'high'],
    ['Puntos 3D', 'sparse.points3d', 'high'], ['Long. media de track', 'sparse.mean_track_length', 'high'],
    ['Error de reproyección (px)', 'sparse.mean_reprojection_error_px', 'low'],
    ['Cámaras georef', 'georef.cameras', null], ['Máscaras', masked, null],
    ['Vértices (malla refinada)', m => get(m, 'meshes.openmvs_mesh_refined.vertices') ?? get(m, 'meshes.openmvs_mesh.vertices'), null],
    ['Caras (malla refinada)', m => get(m, 'meshes.openmvs_mesh_refined.faces') ?? get(m, 'meshes.openmvs_mesh.faces'), null],
    ['Textura (MB)', 'artifacts.texture_obj.size_mb', null], ['Denso', 'dense_backend', null],
    ['Tiempo total (min)', m => m?.timings_total_seconds != null ? Math.round(m.timings_total_seconds / 60) : null, 'low'],
    ...stages.map(s => [`  ${s} (min)`, m => m?.timings_seconds?.[s] != null ? +(m.timings_seconds[s] / 60).toFixed(1) : null, null]),
    ['GPU / nodo', m => [m?.environment?.slurm_gres, m?.environment?.hostname].filter(Boolean).join(' · ') || null, null],
  ];
  return rows.map(([label, key, better]) => {
    const pick = m => typeof key === 'function' ? key(m) : get(m, key);
    const va = pick(a), vb = pick(b);
    let best = null;
    if (better && typeof va === 'number' && typeof vb === 'number' && va !== vb) best = (better === 'high') === (va > vb) ? 'a' : 'b';
    const round = v => typeof v === 'number' && !Number.isInteger(v) ? +v.toFixed(2) : v;
    return { label, a: round(va) ?? null, b: round(vb) ?? null, best };
  }).filter(r => r.a != null || r.b != null);
}

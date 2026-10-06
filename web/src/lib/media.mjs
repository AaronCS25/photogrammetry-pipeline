// Análisis local de una carpeta de captura antes de subirla (sin dependencias: lee cabeceras JPEG/PNG).
import { openSync, readSync, closeSync, readdirSync, statSync, existsSync } from 'node:fs';
import path from 'node:path';

export const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp']);
export const VIDEO_EXTS = new Set(['.mp4', '.mov', '.m4v', '.avi', '.mkv', '.mts', '.m2ts', '.webm']);
export const KINDS = {
  drone_photos: { label: 'Fotos de dron', source: 'drone' },
  phone_photos: { label: 'Fotos de teléfono', source: 'phone' },
  video: { label: 'Video', source: 'video' },
  streetview_export: { label: 'Export de Street View', source: '' },
  other: { label: 'Otra', source: 'otra' },
};
const SIDE = new Set(['.srt', '.SRT']);

function readHead(file, bytes) {
  const fd = openSync(file, 'r');
  try { const buf = Buffer.alloc(bytes); const n = readSync(fd, buf, 0, bytes, 0); return buf.subarray(0, n); }
  finally { closeSync(fd); }
}

// TIFF/EXIF mínimo: Orientation (0x0112), Model (0x0110) y presencia de latitud GPS (IFD 0x8825, tag 2).
function parseExif(buf, start, length) {
  const tiff = buf.subarray(start, start + length);
  if (tiff.length < 8) return {};
  const le = tiff.toString('latin1', 0, 2) === 'II';
  const u16 = o => le ? tiff.readUInt16LE(o) : tiff.readUInt16BE(o);
  const u32 = o => le ? tiff.readUInt32LE(o) : tiff.readUInt32BE(o);
  const entries = ifd => {
    if (ifd + 2 > tiff.length) return [];
    const n = u16(ifd), out = [];
    for (let i = 0; i < n && ifd + 2 + i * 12 + 12 <= tiff.length; i++) {
      const e = ifd + 2 + i * 12;
      out.push({ tag: u16(e), type: u16(e + 2), count: u32(e + 4), value: e + 8 });
    }
    return out;
  };
  const info = {};
  for (const e of entries(u32(4))) {
    if (e.tag === 0x0112) info.orientation = u16(e.value);
    if (e.tag === 0x0110) { const off = e.count > 4 ? u32(e.value) : e.value; info.model = tiff.toString('latin1', off, off + e.count).replace(/\0.*$/, '').trim(); }
    if (e.tag === 0x8825) {
      const gps = entries(u32(e.value));
      const lat = gps.find(g => g.tag === 2);
      if (lat && lat.count === 3) {
        const off = u32(lat.value), deg = off + 8 <= tiff.length ? u32(off) / (u32(off + 4) || 1) : 0;
        info.gps = deg !== 0; // DJI sin fix escribe (0, 0)
      } else info.gps = false;
    }
  }
  return info;
}

/** Dimensiones de píxel + EXIF útil. Lee solo la cabecera (agranda la lectura si el SOF está más lejos). */
export function imageInfo(file) {
  const ext = path.extname(file).toLowerCase();
  if (ext === '.png') {
    const b = readHead(file, 32);
    return b.length >= 24 && b.toString('latin1', 12, 16) === 'IHDR' ? { width: b.readUInt32BE(16), height: b.readUInt32BE(20) } : {};
  }
  if (ext !== '.jpg' && ext !== '.jpeg') return {};
  for (const size of [262144, 2097152]) {
    const b = readHead(file, size), info = {};
    if (b[0] !== 0xff || b[1] !== 0xd8) return {};
    let o = 2;
    while (o + 4 <= b.length) {
      if (b[o] !== 0xff) { o++; continue; }
      const marker = b[o + 1];
      if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) { o += 2; continue; }
      const len = b.readUInt16BE(o + 2);
      if (marker === 0xe1 && b.toString('latin1', o + 4, o + 10) === 'Exif\0\0') Object.assign(info, parseExif(b, o + 10, len - 8));
      if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker) && o + 9 <= b.length) {
        return { ...info, height: b.readUInt16BE(o + 5), width: b.readUInt16BE(o + 7) };
      }
      if (marker === 0xda) break;
      o += 2 + len;
    }
    if (o < b.length) return info; // llegó al scan sin SOF
  }
  return {};
}

function walk(dir, base = dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.name.startsWith('.')) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, base, out);
    else if (e.isFile()) out.push({ rel: path.relative(base, full).split(path.sep).join('/'), full, size: statSync(full).size });
  }
  return out;
}

/**
 * Analiza una carpeta local según el tipo de fuente. Devuelve archivos a subir (rel, size),
 * resumen y avisos. `groups` separa fotos por dimensiones de píxel: una fuente del pipeline
 * exige dimensiones idénticas (single_camera_per_source); las verticales van aparte.
 */
export function scanFolder(folder, kind) {
  if (!folder || !path.isAbsolute(folder)) throw Error('Indica una ruta absoluta de carpeta.');
  if (!existsSync(folder) || !statSync(folder).isDirectory()) throw Error(`No existe la carpeta ${folder}`);
  const warnings = [], errors = [];
  if (kind === 'streetview_export') {
    const files = walk(folder);
    const views = files.filter(f => f.rel.startsWith('fachadas/') && IMAGE_EXTS.has(path.extname(f.rel).toLowerCase()));
    if (!views.length) errors.push('No hay fachadas/*.jpg: ¿es la carpeta descomprimida del export?');
    for (const need of ['indice.csv', 'camaras.json']) if (!files.some(f => f.rel === need)) errors.push(`Falta ${need} en el export.`);
    const dims = {};
    for (const v of views) { const i = imageInfo(v.full); const k = `${i.width}x${i.height}`; dims[k] = (dims[k] || 0) + 1; }
    if (Object.keys(dims).length > 1) errors.push(`Vistas con resoluciones distintas (${Object.entries(dims).map(([k, n]) => `${k}: ${n}`).join(', ')}). El preset exige 1600x1232: borra las vistas de panoramas antiguos.`);
    else if (views.length && !dims['1600x1232']) warnings.push(`Las vistas miden ${Object.keys(dims)[0]}; el preset Street View asume 1600x1232 (camera_params).`);
    const ident = files.some(f => f.rel === 'identificacion.json' || f.rel === 'manifest.json');
    if (!ident) warnings.push('Sin identificacion.json ni manifest.json: la escena no se podrá ubicar sola en el mapa.');
    return { kind, folder, files: files.map(({ rel, size }) => ({ rel, size })), count: files.length, bytes: files.reduce((a, f) => a + f.size, 0),
      summary: { views: views.length, dims }, groups: [{ suffix: '', files: files.map(f => f.rel) }], warnings, errors };
  }
  const all = readdirSync(folder, { withFileTypes: true }).filter(e => e.isFile() && !e.name.startsWith('.'))
    .map(e => { const full = path.join(folder, e.name); return { rel: e.name, full, size: statSync(full).size, ext: path.extname(e.name).toLowerCase() }; });
  const subdirs = readdirSync(folder, { withFileTypes: true }).filter(e => e.isDirectory() && !e.name.startsWith('.')).length;
  if (subdirs) warnings.push(`Se ignoran ${subdirs} subcarpeta(s): solo se suben los archivos de la carpeta elegida.`);
  if (kind === 'video') {
    const videos = all.filter(f => VIDEO_EXTS.has(f.ext)), srts = all.filter(f => SIDE.has(path.extname(f.rel)));
    if (!videos.length) errors.push('No hay videos (.mp4, .mov…) en la carpeta.');
    const files = [...videos, ...srts.filter(s => videos.some(v => v.rel.replace(/\.[^.]+$/, '') === s.rel.replace(/\.[^.]+$/, '')))];
    if (videos.length && !srts.length) warnings.push('Sin .srt de DJI: no habrá telemetría (normal en videos de teléfono).');
    return { kind, folder, files: files.map(({ rel, size }) => ({ rel, size })), count: files.length, bytes: files.reduce((a, f) => a + f.size, 0),
      summary: { videos: videos.length, srt: srts.length }, groups: [{ suffix: '', files: files.map(f => f.rel) }], warnings, errors };
  }
  const photos = all.filter(f => IMAGE_EXTS.has(f.ext));
  const ignored = all.length - photos.length;
  if (!photos.length) errors.push('No hay fotos (.jpg, .png…) en la carpeta.');
  if (ignored) warnings.push(`Se ignoran ${ignored} archivo(s) que no son fotos.`);
  const byDims = new Map();
  let gps = 0, rotated = 0, unknown = 0;
  const models = {};
  for (const p of photos) {
    const i = imageInfo(p.full);
    if (!i.width) unknown++;
    if (i.gps) gps++;
    if (i.orientation && i.orientation !== 1) rotated++;
    if (i.model) models[i.model] = (models[i.model] || 0) + 1;
    const key = i.width ? `${i.width}x${i.height}` : '?';
    if (!byDims.has(key)) byDims.set(key, []);
    byDims.get(key).push(p.rel);
  }
  // Grupo mayoritario en la fuente; el resto en <fuente>_<dims> (p. ej. verticales).
  const sorted = [...byDims.entries()].sort((a, b) => b[1].length - a[1].length);
  const groups = sorted.map(([dims, files], i) => ({ suffix: i === 0 ? '' : `_${dims === '?' ? 'otras' : dims}`, dims, files }));
  if (groups.length > 1) warnings.push(`Dimensiones mixtas (${sorted.map(([d, f]) => `${d}: ${f.length}`).join(', ')}). Cada grupo se sube como fuente aparte (${groups.map(g => g.suffix || 'principal').join(', ')}), porque COLMAP usa una cámara por fuente.`);
  if (photos.length && gps < photos.length) warnings.push(gps ? `${photos.length - gps} foto(s) sin GPS en EXIF.` : 'Ninguna foto tiene GPS en EXIF: sin georef ni matching espacial (usa otro preset o desactiva georef).');
  if (rotated) warnings.push(`${rotated} foto(s) con orientación EXIF ≠ 1 (COLMAP usa los píxeles tal cual).`);
  if (unknown) warnings.push(`${unknown} foto(s) sin dimensiones legibles.`);
  if (photos.length && photos.length < 20) warnings.push('Menos de 20 fotos: la reconstrucción probablemente falle.');
  return { kind, folder, files: photos.map(({ rel, size }) => ({ rel, size })), count: photos.length, bytes: photos.reduce((a, f) => a + f.size, 0),
    summary: { photos: photos.length, gps, rotated, dims: Object.fromEntries(sorted.map(([d, f]) => [d, f.length])), models }, groups, warnings, errors };
}

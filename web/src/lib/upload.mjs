// Subida de una fuente a Khipu: un tar generado aquí se envía por `ssh khipu "tar -x"`.
// Sin rsync (no existe en Windows) y con progreso exacto por bytes. Los archivos se
// escriben tal cual (EXIF intacto). Reanudable: el llamador omite los que ya existen
// en Khipu con el mismo tamaño.
import { spawn } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { options } from './connector.mjs';

const BLOCK = 512;
const MAX_SIZE = 8 ** 11 - 1; // campo de tamaño ustar: 11 dígitos octales (~8 GiB)

function octal(value, length) { return value.toString(8).padStart(length - 1, '0') + '\0'; }

/** Cabecera ustar de un archivo regular (nombre ≤ 100 bytes o prefijo/nombre partido en '/'). */
export function tarHeader(name, size, mtime) {
  if (size > MAX_SIZE) throw Error(`${name}: archivo de más de 8 GiB; no soportado por la subida.`);
  let prefix = '', base = Buffer.from(name);
  if (base.length > 100) {
    const candidates = [...name.matchAll(/\//g)].map(m => m.index).filter(i => Buffer.byteLength(name.slice(0, i)) <= 155 && Buffer.byteLength(name.slice(i + 1)) <= 100);
    if (!candidates.length) throw Error(`${name}: ruta demasiado larga para tar.`);
    const i = candidates[candidates.length - 1];
    prefix = name.slice(0, i); base = Buffer.from(name.slice(i + 1));
  }
  const h = Buffer.alloc(BLOCK);
  base.copy(h, 0);
  h.write(octal(0o644, 8), 100, 'latin1');
  h.write(octal(0, 8), 108, 'latin1');
  h.write(octal(0, 8), 116, 'latin1');
  h.write(octal(size, 12), 124, 'latin1');
  h.write(octal(Math.floor(mtime / 1000), 12), 136, 'latin1');
  h.fill(' ', 148, 156);
  h.write('0', 156, 'latin1');
  h.write('ustar\0', 257, 'latin1');
  h.write('00', 263, 'latin1');
  Buffer.from(prefix).copy(h, 345);
  let sum = 0;
  for (const b of h) sum += b;
  h.write(octal(sum, 7) + ' ', 148, 'latin1');
  return h;
}

/**
 * Escribe un tar en `stream` con los archivos `{full, name, size, mtime}`.
 * `progress(bytesSent, file)` se llama a medida que avanza.
 */
export async function writeTar(stream, files, progress = () => {}, signal) {
  const write = chunk => new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(Error('Subida cancelada.'));
    stream.write(chunk, err => err ? reject(err) : resolve());
  });
  let sent = 0;
  for (const f of files) {
    await write(tarHeader(f.name, f.size, f.mtime || Date.now()));
    let read = 0;
    for await (const chunk of createReadStream(f.full, { highWaterMark: 1 << 20 })) {
      await write(chunk);
      read += chunk.length; sent += chunk.length;
      progress(sent, f);
    }
    if (read !== f.size) throw Error(`${f.name} cambió de tamaño durante la subida.`);
    const pad = (BLOCK - (f.size % BLOCK)) % BLOCK;
    if (pad) await write(Buffer.alloc(pad));
  }
  await write(Buffer.alloc(BLOCK * 2));
}

const SAFE_REMOTE = /^\/[A-Za-z0-9._\/-]+$/;
/** Extrae el tar en <root>/datasets/raw/<scene>/ (el nombre de cada entrada ya incluye la fuente). */
export function uploadTar({ root, scene, files, progress, signal }) {
  if (!SAFE_REMOTE.test(root) || root.includes('..')) throw Error('Ruta remota del proyecto no válida.');
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(scene)) throw Error('Nombre de escena no válido.');
  const dest = `${root}/datasets/raw/${scene}`;
  return new Promise((resolve, reject) => {
    const child = spawn('ssh', ['-T', ...options, '-o', 'Compression=no', process.env.KHIPU_HOST || 'khipu',
      `mkdir -p '${dest}' && tar --no-same-owner -xf - -C '${dest}'`], { shell: false, windowsHide: true });
    let err = '', settled = false;
    const finish = e => { if (settled) return; settled = true; e ? reject(e) : resolve(); };
    child.stderr.on('data', b => { err = (err + b).slice(-4000); });
    child.on('error', e => finish(e.code === 'ENOENT' ? Error('No se encontró «ssh». Instala el cliente OpenSSH de Windows.') : e));
    child.on('close', code => finish(code === 0 ? null : Error(signal?.aborted ? 'Subida cancelada.' : (err.trim() || `ssh terminó con código ${code}`))));
    signal?.addEventListener('abort', () => child.kill());
    child.stdin.on('error', () => {});
    writeTar(child.stdin, files, progress, signal).then(() => child.stdin.end(), e => { child.kill(); finish(e); });
  });
}

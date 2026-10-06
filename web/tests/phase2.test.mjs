import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, createWriteStream } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { parse } from 'yaml';
import { PRESETS, defaultForm, buildConfig, toYaml, parseSbatch, sbatchFromForm, memG, nextName } from '../src/lib/presets.mjs';
import { writeTar, tarHeader } from '../src/lib/upload.mjs';
import { scanFolder, imageInfo } from '../src/lib/media.mjs';
import { derive, indexManzanas } from '../src/lib/derive.mjs';

const temp = () => mkdtempSync(path.join(tmpdir(), 'studio-p2-'));

test('cada preset genera un YAML que se puede leer y respeta el formulario', () => {
  for (const preset of Object.keys(PRESETS)) {
    const form = defaultForm(preset, `${preset}_v1`);
    const cfg = parse(toYaml(buildConfig(form), 'cabecera'));
    assert.equal(cfg.experiment.name, `${preset}_v1`);
    assert.equal(typeof cfg.colmap.feature_extractor.max_image_size, 'number');
  }
  const f = defaultForm('dron', 'x_v1');
  Object.assign(f.georef, { radius_m: 32, height_m: 25 });
  f.quality = 'rapida';
  const cfg = buildConfig(f);
  assert.deepEqual(cfg.georef.roi, { enabled: true, radius_m: 32, below_m: 3, height_m: 25 });
  assert.deepEqual(cfg.masking.backend, ['sam3']);
  assert.deepEqual(cfg.masking.backends.sam3.tiles, [6, 4]);
  assert.equal(cfg.openmvs.refine.enabled, false);
  assert.equal(cfg.openmvs.densify.resolution_level, 2);
  f.masking.enabled = false;
  assert.deepEqual(buildConfig(f).masking, { enabled: false });
  const sv = buildConfig(defaultForm('streetview', 'sv_v1'));
  assert.equal(sv.masking.backends.manual.dir, 'mascaras');
  assert.equal(sv.georef.reference_file, 'indice.csv');
  assert.equal(sv.colmap.feature_extractor.max_image_size, 1600);
});

test('sbatch: SAM 3 nunca va a T4, solo opciones permitidas, RAM', () => {
  const f = defaultForm('dron', 'd_v1');
  f.resources.gpu = 'tesla';
  assert.match(sbatchFromForm(f), /--gres=shard:rtxa6000:8/);
  assert.deepEqual(parseSbatch('--gres=shard:a100:8 --mem=64G --time=0-6:00:00'), ['--gres=shard:a100:8', '--mem=64G', '--time=0-6:00:00']);
  for (const bad of ['--wrap=rm', '--mem=64G;id', '--output=/tmp/x', '$(id)']) assert.throws(() => parseSbatch(bad));
  assert.equal(memG('--mem=96G'), 96);
  assert.equal(nextName('dron', ['dron_v1', 'dron_v3_tiles', 'otro_v9']), 'dron_v4');
});

test('tar generado se extrae con tar real y conserva bytes y nombres', () => {
  const dir = temp();
  try {
    const big = Buffer.alloc(70000, 7), small = Buffer.from('hola');
    writeFileSync(path.join(dir, 'a.jpg'), big); writeFileSync(path.join(dir, 'b.srt'), small);
    const long = 'fachadas/' + 'x'.repeat(95) + '.jpg';
    const files = [{ full: path.join(dir, 'a.jpg'), name: 'phone/a.jpg', size: big.length, mtime: Date.now() },
                   { full: path.join(dir, 'b.srt'), name: long, size: small.length, mtime: Date.now() }];
    const archive = path.join(dir, 'out.tar');
    let last = 0;
    const stream = createWriteStream(archive);
    return writeTar(stream, files, sent => { last = sent; }).then(() => new Promise(r => stream.end(r))).then(() => {
      assert.equal(last, big.length + small.length);
      const out = path.join(dir, 'x'); mkdirSync(out);
      execFileSync('tar', ['-xf', archive.replaceAll('\\', '/'), '-C', out.replaceAll('\\', '/'), '--force-local']);
      assert.deepEqual(readFileSync(path.join(out, 'phone', 'a.jpg')), big);
      assert.deepEqual(readFileSync(path.join(out, ...long.split('/'))), small);
    });
  } finally { setTimeout(() => rmSync(dir, { recursive: true, force: true }), 500); }
});

test('tarHeader rechaza rutas imposibles', () => {
  assert.throws(() => tarHeader('y'.repeat(120), 1, 0));
});

// JPEG mínimo: SOI + APP1 Exif (Orientation, Model, GPS IFD con latitud) + SOF0 + EOI.
function jpeg(width, height, { gps = true, orientation = 1 } = {}) {
  const le = (n, b) => { const x = Buffer.alloc(b); b === 2 ? x.writeUInt16LE(n) : x.writeUInt32LE(n); return x; };
  const entry = (tag, type, count, value) => Buffer.concat([le(tag, 2), le(type, 2), le(count, 4), value]);
  const model = Buffer.from('POCO X6\0');
  const ifd0Count = 3, ifd0Size = 2 + ifd0Count * 12 + 4;
  const modelOff = 8 + ifd0Size, gpsOff = modelOff + model.length;
  const gpsSize = 2 + 12 + 4, latOff = gpsOff + gpsSize;
  const ifd0 = Buffer.concat([le(ifd0Count, 2),
    entry(0x0110, 2, model.length, le(modelOff, 4)),
    entry(0x0112, 3, 1, Buffer.concat([le(orientation, 2), le(0, 2)])),
    entry(0x8825, 4, 1, le(gpsOff, 4)), le(0, 4)]);
  const gpsIfd = Buffer.concat([le(1, 2), entry(0x0002, 5, 3, le(latOff, 4)), le(0, 4)]);
  const lat = Buffer.concat([le(gps ? 12 : 0, 4), le(1, 4), le(8, 4), le(1, 4), le(0, 4), le(1, 4)]);
  const tiff = Buffer.concat([Buffer.from('II'), le(42, 2), le(8, 4), ifd0, model, gpsIfd, lat]);
  const app1 = Buffer.concat([Buffer.from('Exif\0\0'), tiff]);
  const seg = (marker, body) => { const l = Buffer.alloc(2); l.writeUInt16BE(body.length + 2); return Buffer.concat([Buffer.from([0xff, marker]), l, body]); };
  const sof = Buffer.alloc(6); sof[0] = 8; sof.writeUInt16BE(height, 1); sof.writeUInt16BE(width, 3); sof[5] = 0;
  return Buffer.concat([Buffer.from([0xff, 0xd8]), seg(0xe1, app1), seg(0xc0, sof), Buffer.from([0xff, 0xd9])]);
}

test('análisis de carpeta: dimensiones mixtas, GPS, orientación y export Street View', () => {
  const dir = temp();
  try {
    for (let i = 0; i < 25; i++) writeFileSync(path.join(dir, `IMG_${i}.jpg`), jpeg(4624, 3472, { gps: i !== 0 }));
    for (let i = 0; i < 3; i++) writeFileSync(path.join(dir, `V_${i}.jpg`), jpeg(3472, 4624, { orientation: 6 }));
    writeFileSync(path.join(dir, 'notas.txt'), 'x');
    assert.deepEqual(imageInfo(path.join(dir, 'IMG_1.jpg')), { model: 'POCO X6', orientation: 1, gps: true, width: 4624, height: 3472 });
    const r = scanFolder(dir, 'phone_photos');
    assert.equal(r.count, 28);
    assert.deepEqual(r.summary.dims, { '4624x3472': 25, '3472x4624': 3 });
    assert.deepEqual(r.groups.map(g => [g.suffix, g.files.length]), [['', 25], ['_3472x4624', 3]]);
    assert.equal(r.summary.gps, 27);
    assert.equal(r.summary.rotated, 3);
    assert.equal(r.errors.length, 0);
    assert.ok(r.warnings.some(w => w.includes('Dimensiones mixtas')));
    const sv = path.join(dir, 'sv'); mkdirSync(path.join(sv, 'fachadas'), { recursive: true });
    writeFileSync(path.join(sv, 'fachadas', 'p001_01.jpg'), jpeg(1600, 1232));
    writeFileSync(path.join(sv, 'indice.csv'), 'file,lat,lon\n');
    const bad = scanFolder(sv, 'streetview_export');
    assert.ok(bad.errors.some(e => e.includes('camaras.json')));
    writeFileSync(path.join(sv, 'camaras.json'), '{}');
    const ok = scanFolder(sv, 'streetview_export');
    assert.deepEqual(ok.errors, []);
    assert.deepEqual(ok.files.map(f => f.rel).sort(), ['camaras.json', 'fachadas/p001_01.jpg', 'indice.csv']);
    assert.throws(() => scanFolder('relativa', 'phone_photos'));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('un job en cola sin log se asocia a su escena por el envío local', () => {
  const index = indexManzanas({ features: [] });
  const launches = [{ id: 'a'.repeat(32), job: '900', scene: 'mz_abc123', experiment: 'telefono_v1' }];
  const snapshot = { jobs: [{ id: '900', state: 'PENDING', active: true, comment: 'studio:' + 'a'.repeat(32) }],
                     scenes: [{ name: 'mz_abc123', sources: [{ name: 'phone' }], experiments: [] }] };
  const d = derive(snapshot, index, {}, launches);
  assert.equal(d.jobs[0].scene, 'mz_abc123');
  const exp = d.scenes[0].experiments[0];
  assert.deepEqual([exp.name, exp.status, exp.placeholder], ['telefono_v1', 'queued', true]);
});

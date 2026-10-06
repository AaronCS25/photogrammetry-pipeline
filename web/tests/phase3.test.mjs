import test from 'node:test';
import assert from 'node:assert/strict';
import { compareRows, fetchFiles, downloadDir, previewPath } from '../src/lib/results.mjs';

test('comparación: mejor valor según el sentido de la métrica y filas vacías fuera', () => {
  const a = { frames_total: 240, sparse: { registered_images: 180, registration_ratio: 0.75, mean_reprojection_error_px: 0.9 }, timings_total_seconds: 1800, timings_seconds: { sfm: 600 } };
  const b = { frames_total: 240, sparse: { registered_images: 135, registration_ratio: 0.5625, mean_reprojection_error_px: 0.8 }, timings_total_seconds: 3600 };
  const rows = Object.fromEntries(compareRows(a, b).map(r => [r.label.trim(), r]));
  assert.equal(rows['Registradas'].best, 'a');
  assert.equal(rows['% registradas'].a, 75);
  assert.equal(rows['Error de reproyección (px)'].best, 'b');
  assert.equal(rows['Tiempo total (min)'].best, 'a');
  assert.equal(rows['Imágenes'].best, null);
  assert.deepEqual([rows['sfm (min)'].a, rows['sfm (min)'].b], [10, null]);
  assert.ok(!('Puntos 3D' in rows));
});

test('descarga: solo rutas del experimento pedido y nombres simples', async () => {
  const base = { root: '/home/u/photogrammetry-pipeline', scene: 'mz_a', experiment: 'v1', dir: 'C:/tmp/x' };
  const file = (path, name = 'scene_texture.obj') => ({ path, name, size: 1, sha256: 'a'.repeat(64) });
  for (const f of [file('/home/u/photogrammetry-pipeline/outputs/mz_b/v1/mvs/scene_texture.obj'),
                   file('/home/u/photogrammetry-pipeline/outputs/mz_a/v1/../../../../.ssh/id_rsa'),
                   file('/home/u/photogrammetry-pipeline/outputs/mz_a/v1/x;rm -rf ~'),
                   file('/home/u/photogrammetry-pipeline/outputs/mz_a/v1/mvs/a.obj', '../a.obj')]) {
    await assert.rejects(fetchFiles({ ...base, files: [f] }));
  }
  assert.throws(() => downloadDir('../x', 'v1'));
  assert.throws(() => previewPath('mz_a', 'v1', '..', 'a.jpg'));
  assert.throws(() => previewPath('mz_a', 'v1', 'phone', 'a.png'));
});

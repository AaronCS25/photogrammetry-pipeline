import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
import types

# El bridge corre en Linux (Khipu); en Windows no existe fcntl: basta un candado vacío para los tests.
sys.modules.setdefault('fcntl', types.SimpleNamespace(flock=lambda *a: None, LOCK_EX=2))

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).resolve().parents[1] / 'scripts/remote_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        p = patch.object(bridge, 'ROOT', self.root)
        p.start()
        self.addCleanup(p.stop)

    def write(self, relative, text=''):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_identifiers_and_paths(self):
        for value in ('../x', 'a; rm', '$(id)', 'a/b', '..'):
            with self.assertRaises(ValueError):
                bridge.identifier(value)
        with self.assertRaises(ValueError):
            bridge.safe('../fuera')
        with self.assertRaises(ValueError):
            bridge.log({'job': '12; ls'})

    def test_listing(self):
        self.write('datasets/raw/mz_abc123/phone/IMG_1.jpg', 'x' * 10)
        self.write('datasets/raw/mz_abc123/phone/IMG_2.jpg', 'x' * 10)
        self.write('datasets/raw/mz_abc123/mask_overrides/phone/IMG_1.png')
        self.write('datasets/raw/mz_abc123/_ui/exp.yaml')
        self.write('datasets/raw/sv/fachadas/a.jpg')
        self.write('datasets/raw/sv/identificacion.json', json.dumps({'code': 'MZ-BAR-0123456789', 'name': 'Prueba', 'photos': 1}))
        self.write('datasets/raw/sv2/manifest.json', json.dumps({'name': 'B', 'views': [1, 2], 'plan': {'target': {'scope': 'block', 'lot_ids': ['2', '1']}}}))
        exp = 'outputs/mz_abc123/phone_v1/'
        self.write(exp + '.stages/sfm.done', json.dumps({'finished_at': 't', 'elapsed_seconds': 12.5, 'slurm_job_id': '54001'}))
        self.write(exp + '.stages/metrics.done', json.dumps({'slurm_job_id': '54002'}))
        self.write(exp + 'colmap/sparse/0/cameras.bin')
        self.write(exp + 'colmap/sparse/1/cameras.bin')
        self.write(exp + 'mvs/scene_texture.obj')
        self.write(exp + 'metrics/metrics.json', json.dumps({'frames_total': 2, 'sparse': {'registered_images': 2, 'registration_ratio': 1.0},
                                                             'environment': {'slurm_job_id': '54002'}}))
        self.write(exp + 'metrics/georef_info.json', json.dumps({'enu_origin_gps': {'lat': -12.1, 'lon': -77.0}}))
        self.write('outputs/huerfana/e1/.stages/frames.done', '{}')
        self.write('slurm-logs/photogram-54002.out', '== Job 54002 en g002 | config=configs/experiments/p.yaml --scene mz_abc123 --from-stage sfm\n[pipeline] ERROR: algo\n')
        self.write('configs/experiments/p.yaml', '# c\nexperiment:\n  notes: "x"\n  name: phone_v1\n')
        with patch.object(bridge, 'command', side_effect=OSError):
            data = bridge.listing({})
        scenes = {s['name']: s for s in data['scenes']}
        self.assertEqual(scenes['mz_abc123']['sources'], [{'name': 'phone', 'files': 2, 'bytes': 20, 'kinds': {'.jpg': 2}}])
        self.assertEqual(scenes['sv']['identification']['code'], 'MZ-BAR-0123456789')
        self.assertEqual(scenes['sv2']['identification']['code'], bridge.target_code('block', ['1', '2']))
        self.assertTrue(scenes['huerfana']['missingRaw'])
        e = scenes['mz_abc123']['experiments'][0]
        self.assertEqual((e['jobs'], e['fragments'], e['textured']), (['54001', '54002'], 2, True))
        self.assertEqual(e['metrics']['registered_images'], 2)
        self.assertEqual(e['origin']['lat'], -12.1)
        self.assertEqual(bridge.log_header('54002'), {'config': 'configs/experiments/p.yaml', 'scene': 'mz_abc123',
                                                      'experiment': 'phone_v1', 'args': '--from-stage sfm'})
        self.assertEqual(bridge.notices('54002'), ['[pipeline] ERROR: algo'])

    def test_jobs_merge_squeue_and_sacct(self):
        outputs = {
            'sacct': '54001|FAILED|00:10:00|1:0|s|s|e|g002|64G|cpu=16\n54003|PENDING|00:00:00|0:0|s|Unknown|Unknown|None assigned|64G|',
            'squeue': '54003|PENDING|QOSMaxMemoryPerUser|0:00|(null)|gres/shard:rtxa6000:8|64G|studio:abc',
        }
        with patch.object(bridge, 'command', side_effect=lambda args, timeout=30: outputs[args[0]]):
            jobs = bridge.jobs()
        self.assertEqual([j['id'] for j in jobs], ['54003', '54001'])
        self.assertEqual((jobs[0]['reason'], jobs[0]['active']), ('QOSMaxMemoryPerUser', True))
        self.assertEqual(jobs[1]['state'], 'FAILED')
        self.assertEqual((jobs[0]['reqmem'], jobs[0]['comment']), ('64G', 'studio:abc'))

    def test_args_and_config_paths(self):
        self.assertEqual(bridge.check_args(['--from-stage', 'dense', '--force']), ['--from-stage', 'dense', '--force'])
        self.assertEqual(bridge.check_args(['--stages', 'sfm,georef']), ['--stages', 'sfm,georef'])
        for bad in (['--from-stage', 'rm'], ['--config', 'x'], ['--from-stage', 'sfm,dense'], ['; ls']):
            with self.assertRaises(ValueError):
                bridge.check_args(bad)
        self.write('configs/experiments/a.yaml', 'x: 1')
        self.write('datasets/raw/mz_a/_ui/e_v1.yaml', 'x: 1')
        self.assertEqual(bridge.config_path('configs/experiments/a.yaml', 'mz_a'), 'configs/experiments/a.yaml')
        self.assertEqual(bridge.config_path('datasets/raw/mz_a/_ui/e_v1.yaml', 'mz_a'), 'datasets/raw/mz_a/_ui/e_v1.yaml')
        for bad in ('datasets/raw/otra/_ui/e_v1.yaml', '/etc/passwd', 'configs/default.yaml',
                    'configs/experiments/../../x.yaml', 'configs/experiments/nada.yaml'):
            with self.assertRaises(ValueError):
                bridge.config_path(bad, 'mz_a')

    def test_ls_skips_reserved(self):
        self.write('datasets/raw/mz_a/phone/a.jpg', 'abc')
        self.write('datasets/raw/mz_a/_ui/e.yaml', 'x')
        self.write('datasets/raw/mz_a/.oculto', 'x')
        self.assertEqual(bridge.ls({'scene': 'mz_a'}), {'exists': True, 'files': {'phone/a.jpg': 3}})
        self.assertEqual(bridge.ls({'scene': 'mz_b'}), {'exists': False, 'files': {}})

    def submit_env(self):
        self.write('datasets/raw/mz_a/phone/a.jpg', 'abc')
        self.write('datasets/raw/mz_a/_ui/tel_v1.yaml', 'experiment: {name: tel_v1}\n')
        calls = []

        class Run:
            def __init__(self, out):
                self.stdout, self.stderr, self.returncode = out, '', 0

        def fake_run(args, **kw):
            calls.append((args, kw.get('env', {}).get('SBATCH_OPTS')))
            return Run('Submitted batch job 777\n')
        return calls, fake_run

    def request(self, **extra):
        req = {'id': 'b' * 32, 'scene': 'mz_a', 'experiment': 'tel_v1', 'config': 'datasets/raw/mz_a/_ui/tel_v1.yaml',
               'sbatch': ['--gres=shard:rtxa6000:8', '--mem=64G'], 'args': []}
        req.update(extra)
        return req

    def test_submit_writes_receipt_and_is_idempotent(self):
        calls, fake_run = self.submit_env()
        sha = bridge.digest(self.root / 'datasets/raw/mz_a/_ui/tel_v1.yaml')
        with patch.object(bridge.subprocess, 'run', side_effect=fake_run), patch.object(bridge, 'active_jobs', return_value={}):
            first = bridge.submit(self.request(sha256=sha))
            again = bridge.submit(self.request())
        self.assertEqual((first['job'], first['status']), ('777', 'submitted'))
        self.assertEqual(again['job'], '777')
        self.assertEqual(len(calls), 1)  # mismo id: no se vuelve a llamar a sbatch
        args, opts = calls[0]
        self.assertEqual(args, ['./slurm/submit.sh', 'datasets/raw/mz_a/_ui/tel_v1.yaml', 'mz_a'])
        self.assertEqual(opts, '--gres=shard:rtxa6000:8 --mem=64G --comment=studio:' + 'b' * 32)

    def test_submit_rejects_changed_yaml_bad_sbatch_and_duplicates(self):
        calls, fake_run = self.submit_env()
        with patch.object(bridge.subprocess, 'run', side_effect=fake_run), patch.object(bridge, 'active_jobs', return_value={}):
            with self.assertRaises(ValueError):
                bridge.submit(self.request(sha256='0' * 64))
            with self.assertRaises(ValueError):
                bridge.submit(self.request(id='c' * 32, sbatch=['--wrap=rm -rf ~']))
            bridge.submit(self.request(id='d' * 32))
        active = {'777': 'studio:' + 'd' * 32}
        with patch.object(bridge.subprocess, 'run', side_effect=fake_run), patch.object(bridge, 'active_jobs', return_value=active):
            with self.assertRaises(ValueError) as ctx:
                bridge.submit(self.request(id='e' * 32))
            self.assertIn('activo', str(ctx.exception))
        self.assertEqual(len(calls), 1)

    def test_lost_ack_is_reconciled_by_comment(self):
        rid = 'f' * 32
        self.write('datasets/raw/mz_a/_ui/tel_v1.yaml', 'x: 1')
        self.write('datasets/raw/mz_a/_ui/requests/%s.json' % rid, json.dumps(
            {'id': rid, 'scene': 'mz_a', 'experiment': 'tel_v1', 'config': 'datasets/raw/mz_a/_ui/tel_v1.yaml',
             'at': 0, 'status': 'submitting'}))
        no_sbatch = AssertionError('no debe llamar a sbatch')
        with patch.object(bridge.subprocess, 'run', side_effect=no_sbatch), \
                patch.object(bridge, 'active_jobs', return_value={'888': 'studio:' + rid}):
            r = bridge.submit(self.request(id=rid))
        self.assertEqual((r['job'], r['reconciled']), ('888', 'comment'))
        stored = json.loads((self.root / 'datasets/raw/mz_a/_ui/requests' / (rid + '.json')).read_text())
        self.assertEqual(stored['job'], '888')

    def test_mesh_files_lists_texture_and_metrics(self):
        base = 'outputs/mz_a/v1/'
        for rel, text in (('mvs/scene_texture.obj', 'v 0 0 0'), ('mvs/scene_texture.mtl', 'm'), ('mvs/scene_texture.mvs', 'x'),
                          ('mvs/scene_texture_material_0_map_Kd.jpg', 'j'), ('mvs/scene_dense.ply', 'p'),
                          ('metrics/metrics.json', '{}')):
            self.write(base + rel, text)
        r = bridge.mesh_files({'scene': 'mz_a', 'experiment': 'v1'})
        self.assertEqual([f['name'] for f in r['files']], ['scene_texture.mtl', 'scene_texture.obj',
                                                          'scene_texture_material_0_map_Kd.jpg', 'metrics.json'])
        self.assertEqual(r['files'][1]['sha256'], bridge.digest(self.root / base / 'mvs/scene_texture.obj'))
        self.write('outputs/mz_a/v2/mvs/scene_dense.ply', 'p')
        with self.assertRaises(ValueError):
            bridge.mesh_files({'scene': 'mz_a', 'experiment': 'v2'})

    def test_previews_reuse_and_missing_command(self):
        base = 'outputs/mz_a/v1/'
        self.write(base + 'masks/phone/a.jpg.png', 'm')
        with patch.object(bridge.subprocess, 'run', side_effect=AssertionError('no debe ejecutar')):
            with self.assertRaises(ValueError) as ctx:
                bridge.previews({'scene': 'mz_a', 'experiment': 'v1'})
        self.assertIn('git pull', str(ctx.exception))
        self.write(base + 'previews/masks/phone/a.jpg', 'JPEG')
        self.write(base + 'previews/masks/index.json', json.dumps({'items': [{'file': 'phone/a.jpg'}, {'file': '../../../x.jpg'}]}))
        with patch.object(bridge.subprocess, 'run', side_effect=AssertionError('no debe ejecutar')):
            r = bridge.previews({'scene': 'mz_a', 'experiment': 'v1'})
        self.assertTrue(r['reused'])
        self.assertEqual(list(r['files']), ['phone/a.jpg'])

    def test_clone_keeps_only_reused_stages(self):
        src = 'outputs/mz_a/v1/'
        for rel in ('frames/phone/a.jpg', 'masks/phone/a.png', 'colmap/database.db', 'colmap/sparse/0/x.bin',
                    'colmap/sparse_geo/x.bin', 'colmap/undistorted/images/a.jpg', 'mvs/scene_dense.mvs',
                    'metrics/metrics.json', 'metrics/georef_info.json', 'config_resolved.yaml',
                    '.stages/frames.done', '.stages/masks.done', '.stages/sfm.done', '.stages/georef.done',
                    '.stages/dense.done'):
            self.write(src + rel, 'x')
        bridge.clone_experiment('mz_a', 'v1', 'v2', 'georef')
        base = self.root / 'outputs/mz_a/v2'
        got = sorted(p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file())
        self.assertEqual(got, ['.stages/frames.done', '.stages/masks.done', '.stages/sfm.done', 'colmap/database.db',
                               'colmap/sparse/0/x.bin', 'frames/phone/a.jpg', 'masks/phone/a.png'])
        with self.assertRaises(ValueError):
            bridge.clone_experiment('mz_a', 'v1', 'v2', 'dense')  # el destino ya existe
        bridge.clone_experiment('mz_a', 'v1', 'v3', 'mesh')
        self.assertTrue((self.root / 'outputs/mz_a/v3/mvs/scene_dense.mvs').is_file())
        self.assertFalse((self.root / 'outputs/mz_a/v3/metrics/metrics.json').exists())


if __name__ == '__main__':
    unittest.main()

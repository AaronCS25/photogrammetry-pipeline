import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
            'squeue': '54003|PENDING|QOSMaxMemoryPerUser|0:00|(null)|gres/shard:rtxa6000:8',
        }
        with patch.object(bridge, 'command', side_effect=lambda args, timeout=30: outputs[args[0]]):
            jobs = bridge.jobs()
        self.assertEqual([j['id'] for j in jobs], ['54003', '54001'])
        self.assertEqual((jobs[0]['reason'], jobs[0]['active']), ('QOSMaxMemoryPerUser', True))
        self.assertEqual(jobs[1]['state'], 'FAILED')


if __name__ == '__main__':
    unittest.main()

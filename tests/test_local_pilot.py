import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipeline.config import ConfigError, Context, load_config, make_context
from pipeline.executor import CommandError, run_cmd
from pipeline.frames import build_ffmpeg_cmd
from pipeline.state import bind_run
from tools.preview_clouds import read_cloud


class LocalPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def context(self):
        cfg = load_config(None)
        cfg['paths'] = {'raw_root': str(self.root/'raw'), 'output_root': str(self.root/'outputs')}
        cfg['runtime']['verify_cache'] = True
        raw = self.root/'raw'/'scene'
        raw.mkdir(parents=True, exist_ok=True)
        (raw/'input.mp4').write_bytes(b'original-input')
        ctx = Context(cfg, 'scene', 'experiment')
        ctx.prepare()
        return ctx

    def test_nonzero_video_interval_is_ten_seconds_not_end_timestamp(self):
        # An actual FFmpeg regression: [5,15) must yield ten 1-FPS frames,
        # rather than fifteen frames after seeking to second five.
        source = self.root/'source.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x64:rate=10',
                        '-t','20','-c:v','mpeg4',str(source)], check=True, capture_output=True)
        command = build_ffmpeg_cmd(source, self.root/'frame_%03d.jpg',
                                   {'start':'00:00:05','end':'00:00:15','fps':1,'format':'jpg'})
        subprocess.run(command, check=True, capture_output=True)
        self.assertEqual(len(list(self.root.glob('frame_*.jpg'))), 10)

    def test_reversed_video_interval_is_rejected(self):
        with self.assertRaises(CommandError):
            build_ffmpeg_cmd(Path('video.mp4'), Path('out_%03d.jpg'), {'start':10,'end':5})

    def test_changed_inputs_cannot_reuse_completed_experiment(self):
        ctx = self.context()
        lock = bind_run(ctx)
        lock.close()
        (ctx.raw_dir/'input.mp4').write_bytes(b'different-video')
        with self.assertRaises(CommandError):
            bind_run(ctx)

    def test_changed_dense_parameters_cannot_silently_reuse_markers(self):
        ctx = self.context()
        bind_run(ctx).close()
        ctx.cfg['openmvs']['densify']['resolution_level'] += 1
        with self.assertRaises(CommandError):
            bind_run(ctx)

    def test_concurrent_experiment_is_rejected(self):
        ctx = self.context()
        lock = bind_run(ctx)
        self.addCleanup(lock.close)
        with self.assertRaises(CommandError):
            bind_run(ctx)

    def test_same_experiment_can_resume_after_releasing_lock(self):
        ctx = self.context()
        bind_run(ctx).close()
        bind_run(ctx).close()

    def test_traversal_in_scene_is_rejected(self):
        with self.assertRaises(ConfigError):
            make_context(load_config(None), '../another-scene')

    def test_low_disk_prevents_launching_command(self):
        usage = SimpleNamespace(free=1024)
        with patch.dict(os.environ, {'PG_DATA_ROOT':str(self.root),'PG_MIN_FREE_GIB':'20'}), \
                patch('pipeline.executor.shutil.disk_usage', return_value=usage), \
                patch('pipeline.executor.subprocess.Popen') as launch:
            with self.assertRaises(CommandError):
                run_cmd(['should-not-run'], self.root/'log')
            launch.assert_not_called()

    def test_live_command_output_is_logged_and_failure_is_not_success(self):
        with patch.dict(os.environ, {'PG_DATA_ROOT':str(self.root),'PG_MIN_FREE_GIB':'0'}):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(CommandError):
                    run_cmd(['python3','-c','print("visible-log"); raise SystemExit(7)'], self.root/'log')
        self.assertIn('visible-log', (self.root/'log').read_text())

    def cloud_with_lists(self):
        path = self.root/'dense.ply'
        header = (b'ply\nformat binary_little_endian 1.0\nelement vertex 2\n'
                  b'property float32 x\nproperty float32 y\nproperty float32 z\n'
                  b'property uint8 red\nproperty uint8 green\nproperty uint8 blue\n'
                  b'property list uint8 uint32 view_indices\nproperty list uint8 float32 view_weights\nend_header\n')
        first = struct.pack('<fffBBBBIIBff',1,2,3,10,20,30,2,4,9,2,0.5,0.8)
        second = struct.pack('<fffBBBBIBf',7,8,9,40,50,60,1,3,1,0.6)
        path.write_bytes(header+first+second)
        return path

    def test_openmvs_variable_length_vertex_lists_do_not_corrupt_coordinates(self):
        sample, info = read_cloud(self.cloud_with_lists())
        self.assertEqual(info['vertices'],2)
        self.assertEqual(sample.tolist(),[[1,2,3,10,20,30],[7,8,9,40,50,60]])

    def test_truncated_ply_is_not_accepted_as_valid_cloud(self):
        path=self.cloud_with_lists()
        path.write_bytes(path.read_bytes()[:-3])
        with self.assertRaises(ValueError):
            read_cloud(path)


if __name__ == '__main__':
    unittest.main()

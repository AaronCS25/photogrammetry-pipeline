import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from pipeline.config import ConfigError, Context, load_config
from pipeline.executor import CommandError, StageRunner
from pipeline.model_assets import digest, verify_manifest
from pipeline.state import bind_run


class SegmentationStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        cfg = load_config(None)
        cfg['paths'] = {'raw_root': str(self.root/'raw'), 'output_root': str(self.root/'outputs')}
        cfg['runtime'].update(verify_cache=True, multi_image=True)
        cfg['masking']['enabled'] = False
        (self.root/'raw/scene').mkdir(parents=True)
        (self.root/'raw/scene/video.mp4').write_bytes(b'video')
        self.ctx = Context(cfg, 'scene', 'experiment')
        self.ctx.prepare()
        self.env = {'PG_RECON_IMAGE_ID': 'sha256:recon', 'PG_SEG_IMAGE_ID': 'sha256:seg',
                    'PG_RUN_ROLE': 'reconstruction', 'PG_IMAGE_ID': 'sha256:recon'}

    def test_reconstruction_and_segmentation_share_declared_identity(self):
        with patch.dict(os.environ, self.env):
            bind_run(self.ctx, ['frames']).close()
        with patch.dict(os.environ, dict(self.env, PG_RUN_ROLE='segmentation', PG_IMAGE_ID='sha256:seg')):
            bind_run(self.ctx, ['masks']).close()
        manifest = json.loads((self.ctx.out_dir/'run_manifest.json').read_text())
        self.assertEqual(manifest['schema'], 2)
        self.assertNotIn('image_id', manifest)

    def test_undeclared_actual_image_is_rejected(self):
        with patch.dict(os.environ, dict(self.env, PG_IMAGE_ID='sha256:wrong')):
            with self.assertRaises(CommandError):
                bind_run(self.ctx, ['frames'])

    def test_masks_cannot_run_in_reconstruction_role(self):
        with patch.dict(os.environ, self.env):
            with self.assertRaises(CommandError):
                bind_run(self.ctx, ['masks'])

    def test_declared_image_change_cannot_reuse_experiment(self):
        with patch.dict(os.environ, self.env):
            bind_run(self.ctx, ['frames']).close()
        with patch.dict(os.environ, dict(self.env, PG_SEG_IMAGE_ID='sha256:other')):
            with self.assertRaises(CommandError):
                bind_run(self.ctx, ['frames'])

    def test_failed_masks_do_not_leave_success_marker(self):
        runner = StageRunner(self.ctx.markers_dir)
        def fail():
            raise CommandError('model failed')
        with self.assertRaises(CommandError):
            runner.run('masks', fail)
        self.assertFalse(runner.is_done('masks'))

    def test_deleted_artifact_is_not_silently_resumed(self):
        artifact = self.root/'product'
        def validate(stage):
            if not artifact.exists():
                raise CommandError('deleted product')
        runner = StageRunner(self.ctx.markers_dir, validator=validate)
        runner.run('frames', lambda: artifact.write_text('valid'))
        artifact.unlink()
        with self.assertRaises(CommandError):
            runner.run('frames', lambda: None)

    def test_portable_configuration_requires_declared_root(self):
        config = self.root/'portable.yaml'
        config.write_text('paths:\n  raw_root: ${PG_TEST_ROOT}/datasets/raw\n')
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                load_config(str(config))
        with patch.dict(os.environ, {'PG_TEST_ROOT': str(self.root)}):
            self.assertEqual(load_config(str(config))['paths']['raw_root'], str(self.root/'datasets/raw'))

    def model_manifest(self):
        model = self.root/'model'
        model.mkdir()
        weights = model/'model.bin'
        weights.write_bytes(b'known weights')
        path = model/'weights-manifest.json'
        data = {'model_id': 'test/model', 'revision': 'pinned', 'files': {
            'model.bin': {'bytes': weights.stat().st_size, 'sha256': digest(weights)}}}
        path.write_text(json.dumps(data))
        return path

    def test_corrupt_weight_rejected(self):
        path = self.model_manifest()
        verify_manifest(path)
        (path.parent/'model.bin').write_bytes(b'wrong weights')
        with self.assertRaises(CommandError):
            verify_manifest(path)

    def test_changed_prompts_reject_cached_run(self):
        with patch.dict(os.environ, self.env):
            bind_run(self.ctx, ['frames']).close()
            self.ctx.cfg['masking']['backends']['sam3']['prompts'] = ['different object']
            with self.assertRaises(CommandError):
                bind_run(self.ctx, ['frames'])

    def test_changed_weight_revision_rejects_cached_run(self):
        path = self.model_manifest()
        self.ctx.cfg['masking'].update(enabled=True, backend='sam3')
        self.ctx.cfg['masking']['backends']['sam3']['checkpoint'] = str(path.parent/'model.bin')
        self.ctx.cfg['runtime']['model_manifests'] = {'sam3': str(path)}
        with patch.dict(os.environ, self.env):
            bind_run(self.ctx, ['frames']).close()
            data = json.loads(path.read_text())
            data['revision'] = 'another revision'
            path.write_text(json.dumps(data))
            with self.assertRaises(CommandError):
                bind_run(self.ctx, ['frames'])


@unittest.skipUnless(importlib.util.find_spec('pycolmap'), 'requires reconstruction image')
class MaskGeometryTests(unittest.TestCase):
    def test_real_colmap_models_generate_alias_and_reject_deleted_mask(self):
        import pycolmap
        from PIL import Image
        from pipeline.mask_geometry import prepare_dense_masks, require_dense_masks
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, workspace = root/'original', root/'workspace'
            for folder in [original, workspace/'sparse']:
                folder.mkdir(parents=True)
                model = pycolmap.Reconstruction()
                model.add_camera(pycolmap.Camera(camera_id=1, model='PINHOLE', width=16, height=12, params=[10, 10, 8, 6]))
                model.add_image(pycolmap.Image(name='drone/frame.jpg', cam_from_world=pycolmap.Rigid3d(), camera_id=1, id=1))
                self.assertEqual(model.reg_image_ids(), {1})
                model.write(str(folder))
            ctx = SimpleNamespace(colmap_dir=root/'colmap', masks_dir=root/'masks')
            (ctx.masks_dir/'drone').mkdir(parents=True)
            mask = np.full((12, 16), 255, np.uint8)
            mask[2:6, 4:10] = 0
            Image.fromarray(mask).save(ctx.masks_dir/'drone/frame.jpg.png')
            result = prepare_dense_masks(ctx, workspace, original)
            require_dense_masks(ctx, workspace)
            with Image.open(result/'frame.mask.png') as alias:
                np.testing.assert_array_equal(np.array(alias), mask)
            (result/'frame.mask.png').unlink()
            with self.assertRaises(CommandError):
                require_dense_masks(ctx, workspace)

    def test_identity_keeps_every_binary_pixel(self):
        import pycolmap
        from pipeline.mask_geometry import remap_mask
        c = pycolmap.Camera(model='PINHOLE', width=64, height=48, params=[50, 50, 32, 24])
        mask = np.where(np.indices((48, 64)).sum(axis=0) % 2, 255, 0).astype(np.uint8)
        np.testing.assert_array_equal(remap_mask(mask, c, c), mask)

    def test_radial_projection_matches_independent_formula(self):
        import pycolmap
        from pipeline.mask_geometry import sampling_map
        original = pycolmap.Camera(model='SIMPLE_RADIAL', width=64, height=48, params=[40, 32, 24, 0.2])
        target = pycolmap.Camera(model='PINHOLE', width=32, height=24, params=[20, 20, 16, 12])
        indices, valid = sampling_map(original, target)
        for x, y in ((0, 0), (16, 12), (27, 19)):
            nx, ny = (x + .5 - 16)/20, (y + .5 - 12)/20
            scale = 1 + .2*(nx*nx + ny*ny)
            expected = np.floor([40*nx*scale+32, 40*ny*scale+24]).astype(int)
            np.testing.assert_array_equal(indices[y*32+x], expected)
            self.assertEqual(bool(valid[y*32+x]), bool(0 <= expected[0] < 64 and 0 <= expected[1] < 48))

    def test_out_of_bounds_are_excluded_not_wrapped(self):
        import pycolmap
        from pipeline.mask_geometry import remap_mask
        source = pycolmap.Camera(model='PINHOLE', width=8, height=8, params=[4, 4, 4, 4])
        target = pycolmap.Camera(model='PINHOLE', width=8, height=8, params=[4, 4, -100, -100])
        self.assertFalse(remap_mask(np.full((8, 8), 255, np.uint8), source, target).any())

    def test_nonbinary_mask_rejected(self):
        import pycolmap
        from pipeline.mask_geometry import remap_mask
        c = pycolmap.Camera(model='PINHOLE', width=8, height=8, params=[4, 4, 4, 4])
        with self.assertRaises(CommandError):
            remap_mask(np.full((8, 8), 127, np.uint8), c, c)

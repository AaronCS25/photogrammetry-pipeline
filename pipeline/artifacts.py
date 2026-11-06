"""Validate critical stage products before accepting completion or resumption."""
from .executor import CommandError


def validate_stage(ctx, stage):
    from .masks import frame_images, require_masks
    from .sfm import best_model_dir, undistorted_dir
    def require(path):
        if not path.is_file() or not path.stat().st_size:
            raise CommandError(f'Missing/empty product of {stage}: {path}; use a new experiment or explicitly rebuild')
    if stage == 'frames':
        from PIL import Image
        sources = frame_images(ctx.frames_dir)
        if not sources:
            raise CommandError('No frame images')
        for images in sources.values():
            for path in images:
                with Image.open(path) as image:
                    image.verify()
    elif stage == 'masks':
        require_masks(ctx)
        require(ctx.metrics_dir / 'masks_info.json')
    elif stage == 'sfm':
        for name in ('cameras.bin', 'images.bin', 'points3D.bin'):
            require(best_model_dir(ctx) / name)
        require(ctx.colmap_dir / 'sparse.ply')
    elif stage == 'undistort':
        import pycolmap
        workspace = undistorted_dir(ctx)
        require(workspace / 'sparse/cameras.bin')
        model = pycolmap.Reconstruction(str(workspace / 'sparse'))
        for image_id in sorted(model.reg_image_ids()):
            image = model.images[image_id]
            require(workspace / 'images' / image.name)
        masking = ctx.cfg.get('masking') or {}
        if masking.get('enabled') and masking.get('apply_to_dense'):
            from .mask_geometry import require_dense_masks
            require_dense_masks(ctx, workspace)
    elif stage == 'dense':
        from .metrics import parse_ply_counts
        if ctx.cfg.get('dense', {}).get('backend') != 'openmvs':
            return
        require(ctx.mvs_dir / 'scene_dense.mvs')
        cloud = ctx.mvs_dir / 'scene_dense.ply'
        require(cloud)
        if not parse_ply_counts(cloud).get('vertex', 0):
            raise CommandError('Empty dense cloud')

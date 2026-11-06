"""Transform binary masks with the actual original/undistorted COLMAP cameras."""
import json
from pathlib import Path

from .executor import CommandError


def sampling_map(original, target):
    import numpy as np
    y, x = np.indices((target.height, target.width), dtype=np.float64)
    # COLMAP uses pixel centers at (0.5, 0.5), not array indices (0, 0).
    points = np.column_stack((x.ravel() + 0.5, y.ravel() + 0.5))
    source = original.img_from_cam(target.cam_from_img(points))
    valid = (np.isfinite(source).all(axis=1) & (source[:, 0] >= 0) & (source[:, 0] < original.width)
             & (source[:, 1] >= 0) & (source[:, 1] < original.height))
    indices = np.floor(np.where(np.isfinite(source), source, 0)).astype(np.int64)
    return indices, valid


def remap_mask(mask, original, target, mapping=None):
    import numpy as np
    if mask.shape != (original.height, original.width) or not np.isin(mask, [0, 255]).all():
        raise CommandError('Mask must be binary and match the original camera dimensions')
    indices, valid = mapping if mapping is not None else sampling_map(original, target)
    result = np.zeros(target.height * target.width, dtype=np.uint8)
    result[valid] = mask[indices[valid, 1], indices[valid, 0]]
    return result.reshape(target.height, target.width)


def undistorted_masks_dir(ctx):
    return ctx.colmap_dir / 'undistorted_masks'


def prepare_dense_masks(ctx, workspace, original_model):
    import numpy as np
    import pycolmap
    from PIL import Image
    from .masks import colmap_mask_path, _link_openmvs_aliases
    original = pycolmap.Reconstruction(str(original_model))
    target = pycolmap.Reconstruction(str(workspace / 'sparse'))
    originals = {original.images[i].name: original.images[i] for i in sorted(original.reg_image_ids())}
    root = undistorted_masks_dir(ctx)
    root.mkdir(parents=True, exist_ok=True)
    cache, sources, records = {}, {}, []
    for image_id in sorted(target.reg_image_ids()):
        im = target.images[image_id]
        name = Path(im.name)
        if name.is_absolute() or '..' in name.parts or len(name.parts) != 2:
            raise CommandError(f'Expected source/image name, got {name}')
        before = originals[im.name]
        camera = original.cameras[before.camera_id]
        out_camera = target.cameras[im.camera_id]
        key = (before.camera_id, im.camera_id)
        if key not in cache:
            cache[key] = sampling_map(camera, out_camera)
        source = ctx.masks_dir / name.parent / colmap_mask_path(name)
        with Image.open(source) as image:
            mask = remap_mask(np.array(image.convert('L')), camera, out_camera, cache[key])
        destination = root / name.parent / colmap_mask_path(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(mask).save(destination)
        sources.setdefault(name.parts[0], []).append(name)
        records.append({'image': im.name, 'width': out_camera.width, 'height': out_camera.height,
                        'masked_ratio': float((mask == 0).mean())})
    if not records:
        raise CommandError('No registered images for dense masks')
    _link_openmvs_aliases(ctx, sources, mask_root=root)
    (root / 'geometry.json').write_text(json.dumps({'convention': 'COLMAP pixel centers; nearest neighbor; outside=0',
                                                  'images': records}, indent=2) + '\n')
    return root


def require_dense_masks(ctx, workspace):
    import numpy as np
    import pycolmap
    from PIL import Image
    from .masks import openmvs_mask_path
    root = undistorted_masks_dir(ctx)
    model = pycolmap.Reconstruction(str(workspace / 'sparse'))
    for image_id in sorted(model.reg_image_ids()):
        im = model.images[image_id]
        path = root / openmvs_mask_path(Path(im.name))
        if not path.is_file():
            raise CommandError(f'Missing undistorted mask: {path}; rerun undistort in a new experiment')
        with Image.open(path) as mask:
            camera = model.cameras[im.camera_id]
            if mask.size != (camera.width, camera.height) or not np.isin(np.array(mask.convert('L')), [0, 255]).all():
                raise CommandError(f'Undistorted mask size mismatch: {path}')
    return root

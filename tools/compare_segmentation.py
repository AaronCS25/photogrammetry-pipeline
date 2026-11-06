"""Compare completed experiments, identical frame hashes and feature exclusion."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.model_assets import digest
from tools.preview_clouds import read_cloud, render


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--version', default='v1')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    reference, rows = None, []
    for name in ['control', 'segformer', 'sam3', 'combined']:
        run = args.root/f'seg025_0_10s_{name}_{args.version}'
        metrics = json.loads((run/'metrics/metrics.json').read_text())
        frames = {str(f.relative_to(run/'frames')): digest(f) for f in sorted((run/'frames').glob('*/*.jpg'))}
        reference = frames if reference is None else reference
        assert frames == reference and len(frames) == 20, 'Different extracted frames'
        row = {'variant': name, 'run': str(run), 'frames': len(frames), 'sparse': metrics['sparse'],
               'dense': metrics['dense'], 'timings_seconds': metrics['timings_seconds'], 'identical_frame_hashes': True}
        info = run/'metrics/masks_info.json'
        if info.exists():
            row['masks'] = json.loads(info.read_text())
            db = sqlite3.connect(f'file:{run/"colmap/database.db"}?mode=ro', uri=True)
            total, forbidden = 0, 0
            for image_name, count, columns, blob in db.execute('SELECT images.name,keypoints.rows,keypoints.cols,keypoints.data FROM keypoints JOIN images USING(image_id)'):
                xy = np.frombuffer(blob, np.float32).reshape(count, columns)[:, :2]
                with Image.open(run/'masks'/Path(image_name).parent/(Path(image_name).name+'.png')) as image:
                    excluded = np.array(image) == 0
                # Ignore one-pixel boundaries: COLMAP and array centers can differ at an edge.
                inside = excluded.copy()
                inside[0, :] = inside[-1, :] = False
                inside[:, 0] = inside[:, -1] = False
                h, w = excluded.shape
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        inside[1:-1, 1:-1] &= excluded[1+dy:h-1+dy, 1+dx:w-1+dx]
                x = np.floor(xy[:, 0]).astype(int).clip(0, excluded.shape[1]-1)
                y = np.floor(xy[:, 1]).astype(int).clip(0, excluded.shape[0]-1)
                total += count
                forbidden += int(inside[y, x].sum())
            db.close()
            row['feature_exclusion_check'] = {'keypoints': total, 'deep_inside_exclusion': forbidden}
        previews = run/'previews/clouds'
        previews.mkdir(parents=True, exist_ok=True)
        clouds = {}
        for kind, relative in [('sparse', 'colmap/sparse.ply'), ('dense', 'mvs/scene_dense.ply')]:
            sample, cloud = read_cloud(run/relative)
            clouds[kind] = cloud
            render(sample, f'{name} {kind}: {cloud["vertices"]:,} points', previews/(kind+'.png'))
        (previews/'cloud-inspection.json').write_text(json.dumps(clouds, indent=2)+'\n')
        row['cloud_integrity'] = clouds
        rows.append(row)
    (args.output/'comparison.json').write_text(json.dumps(rows, indent=2)+'\n')
    print(json.dumps([{'variant': r['variant'], 'registered': r['sparse']['registered_images'],
                      'sparse_points': r['sparse']['points3d'], 'dense_points': r['dense']['openmvs_points'],
                      'reprojection_px': r['sparse']['mean_reprojection_error_px'],
                      'feature_exclusion_check': r.get('feature_exclusion_check'),
                      'mean_excluded': r.get('masks', {}).get('sources', {}).get('drone', {}).get('mean_masked_ratio')}
                     for r in rows], indent=2))


if __name__ == '__main__':
    main()

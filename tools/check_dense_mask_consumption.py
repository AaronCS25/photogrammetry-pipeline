"""Prove OpenMVS consumes masks: identical cameras/images, white vs black masks."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pycolmap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.metrics import parse_ply_counts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    workspace = args.run_dir/'colmap/undistorted'
    model = pycolmap.Reconstruction(str(workspace/'sparse'))
    args.output.mkdir(parents=True, exist_ok=False)
    report = {}
    for name, value in [('white', 255), ('black', 0)]:
        root = args.output/name
        root.mkdir()
        (root/'images').symlink_to(workspace/'images', target_is_directory=True)
        masks = root/'masks'
        masks.mkdir()
        for image_id in sorted(model.reg_image_ids()):
            image = model.images[image_id]
            camera = model.cameras[image.camera_id]
            Image.new('L', (camera.width, camera.height), value).save(masks/(Path(image.name).stem+'.mask.png'))
        conversion = ['InterfaceCOLMAP', '-w', str(root), '-i', str(workspace), '-o', 'scene.mvs']
        with (root/'convert.log').open('w') as log:
            subprocess.run(conversion, stdout=log, stderr=subprocess.STDOUT, check=True)
        command = ['DensifyPointCloud', '-w', str(root), 'scene.mvs', '-o', 'scene_dense.mvs',
                   '--mask-path', str(masks), '--ignore-mask-label', '0', '--resolution-level', '1',
                   '--number-views', '4', '--max-threads', '8', '--max-resolution', '1280', '--min-resolution', '640']
        with (root/'dense.log').open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        cloud = root/'scene_dense.ply'
        count = parse_ply_counts(cloud).get('vertex', 0) if cloud.exists() else 0
        report[name] = {'returncode': result.returncode, 'points': count, 'command': command}
        print(name, result.returncode, count, flush=True)
    report['passed'] = report['white']['returncode'] == 0 and report['white']['points'] > 0 and report['black']['points'] == 0
    (args.output/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    if not report['passed']:
        raise SystemExit('OpenMVS did not exhibit expected mask consumption; inspect both logs')


if __name__ == '__main__':
    main()

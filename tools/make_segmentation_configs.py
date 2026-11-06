"""Generate portable controlled comparisons from the certified pilot settings."""
import argparse
import copy
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=ROOT/'configs/experiments/segmentation-025-0-10s')
    p.add_argument('--version', default='v1')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = yaml.safe_load((ROOT/'configs/experiments/l507_012_0_10s.yaml').read_text())
    for variant, backend in [('control', 'segformer'), ('segformer', 'segformer'), ('sam3', 'sam3'), ('combined', ['segformer', 'sam3'])]:
        cfg = copy.deepcopy(base)
        cfg['experiment'] = {'name': f'seg025_0_10s_{variant}_{args.version}',
                             'notes': 'Controlled comparison: identical 20 frames, cameras and reconstruction parameters.'}
        cfg['paths'] = {'raw_root': '${PG_DATA_ROOT}/datasets/raw', 'output_root': '${PG_DATA_ROOT}/outputs'}
        cfg['runtime'].update(multi_image=True, model_manifests={
            model: '${PG_DATA_ROOT}/models/'+model+'/weights-manifest.json' for model in ['segformer', 'sam3']})
        cfg['masking'] = {
            'enabled': variant != 'control', 'backend': backend, 'apply_to_dense': True, 'dilate_px': 3,
            'classes': ['person', 'rider', 'car', 'truck', 'bus', 'train', 'motorcycle', 'bicycle', 'sky'],
            'backends': {
                'segformer': {'model_id': '${PG_DATA_ROOT}/models/segformer', 'inference_size': 1024, 'dilate_px': 3},
                'sam3': {'checkpoint': '${PG_DATA_ROOT}/models/sam3/sam3.pt',
                         'prompts': ['person', 'car', 'truck', 'bus', 'motorcycle', 'bicycle', 'sky', 'power line', 'utility pole'],
                         'score_threshold': .5, 'dilate_px': 3, 'tiles': None},
            },
        }
        target = args.output / f'{variant}.yaml'
        if target.exists():
            raise SystemExit(f'Refusing to overwrite {target}; choose a new output directory')
        target.write_text(yaml.safe_dump(cfg, sort_keys=False))
        print(target)


if __name__ == '__main__':
    main()

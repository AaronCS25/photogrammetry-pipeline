"""Run both segmenters offline on three representative frames and save overlays."""
import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageOps
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.config import load_config
from pipeline.mask_backends import get_backend


def overlay(image, mask):
    original = image.convert('RGB')
    red = Image.new('RGB', original.size, (255, 0, 0))
    exclusion = Image.fromarray(np.where(mask == 0, 140, 0).astype(np.uint8))
    return Image.composite(red, original, exclusion)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True)
    p.add_argument('--frames', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    cfg = load_config(args.config)['masking']
    images = sorted(args.frames.glob('*/*.jpg'))
    assert len(images) >= 3
    selected = [images[i] for i in [0, len(images)//2, len(images)-1]]
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'gpu': torch.cuda.get_device_name(), 'torch': torch.__version__, 'frames': [p.name for p in selected], 'backends': {}}
    for name in ('segformer', 'sam3'):
        out = args.output/name
        out.mkdir()
        torch.cuda.reset_peak_memory_stats()
        started = time.monotonic()
        results = get_backend(name).generate(selected, out, cfg, None)
        torch.cuda.synchronize()
        report['backends'][name] = {'seconds': time.monotonic()-started,
                                    'peak_allocated_mib': torch.cuda.max_memory_allocated()/1024**2,
                                    'peak_reserved_mib': torch.cuda.max_memory_reserved()/1024**2,
                                    'results': results}
        torch.cuda.empty_cache()
    sheets = []
    for path in selected:
        with Image.open(path) as source:
            original = source.convert('RGB')
        masks = {}
        for name in ('segformer', 'sam3'):
            with Image.open(args.output/name/(path.name+'.png')) as mask:
                masks[name] = np.array(mask)
        masks['combined'] = np.minimum(masks['segformer'], masks['sam3'])
        row = Image.new('RGB', (640*4, 400), 'white')
        draw = ImageDraw.Draw(row)
        for i, name in enumerate(['original', 'segformer', 'sam3', 'combined']):
            panel = original if name == 'original' else overlay(original, masks[name])
            panel = ImageOps.contain(panel, (640, 360))
            row.paste(panel, (i*640, 40))
            ratio = '' if name == 'original' else f' / excluded {(masks[name]==0).mean():.1%}'
            draw.text((i*640+8, 8), name+ratio, fill='black')
        sheets.append(row)
    sheet = Image.new('RGB', (2560, 400*len(sheets)), 'white')
    for i, row in enumerate(sheets):
        sheet.paste(row, (0, 400*i))
    sheet.save(args.output/'overlays.png')
    (args.output/'smoke.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()

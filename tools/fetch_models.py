"""Download revision-pinned segmentation assets without persisting HF credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

MODELS = {
    'segformer': ('nvidia/segformer-b5-finetuned-cityscapes-1024-1024', ['*.json', '*.safetensors', 'pytorch_model.bin']),
    'sam3': ('facebook/sam3', ['config.json', 'sam3.pt']),
}


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def main():
    from huggingface_hub import HfApi, snapshot_download
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--token-stdin', action='store_true')
    p.add_argument('--model', choices=['all', *MODELS], default='all')
    args = p.parse_args()
    token = os.environ.pop('HF_TOKEN', None)
    if args.token_stdin:
        print('MODEL_DOWNLOAD_TOKEN:', flush=True)
        token = sys.stdin.readline().strip()
    args.root.mkdir(parents=True, exist_ok=True)
    for name, (model_id, patterns) in MODELS.items():
        if args.model not in ('all', name):
            continue
        target = args.root / name
        previous = target / 'weights-manifest.json'
        revision = json.loads(previous.read_text())['revision'] if previous.exists() else HfApi().model_info(model_id, token=token).sha
        snapshot_download(model_id, revision=revision, local_dir=target,
                          cache_dir=args.root / 'download-cache', allow_patterns=patterns, token=token)
        files = {str(f.relative_to(target)): {'bytes': f.stat().st_size, 'sha256': sha256(f)}
                 for f in sorted(target.rglob('*')) if f.is_file() and '.cache' not in f.relative_to(target).parts
                 and f != previous}
        assert files, 'No model assets downloaded'
        manifest = {'model_id': model_id, 'revision': revision, 'files': files}
        previous.write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps({'model': name, 'revision': revision, 'files': len(files),
                          'bytes': sum(f['bytes'] for f in files.values())}), flush=True)


if __name__ == '__main__':
    main()

"""Integrity checks for externally stored segmentation weights."""
import hashlib
import json
from pathlib import Path

from .executor import CommandError


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text())
        if not data.get('files') or not data.get('revision'):
            raise ValueError('Missing revision/files')
        loadable = {p.name for p in path.parent.iterdir()
                    if p.is_file() and p != path and p.suffix in ('.pt', '.bin', '.safetensors', '.json')}
        if not loadable.issubset(data['files']):
            raise ValueError('Unmanifested loadable model assets')
        for name, item in data['files'].items():
            asset = (path.parent / name).resolve()
            if not asset.is_relative_to(path.parent.resolve()):
                raise ValueError('Asset outside model directory')
            if asset.stat().st_size != item['bytes'] or digest(asset) != item['sha256']:
                raise ValueError(f'Asset checksum mismatch: {name}')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CommandError(f'Invalid model manifest {path}: {exc}') from exc
    return {'model_id': data['model_id'], 'revision': data['revision'], 'manifest_sha256': digest(path)}

"""Opt-in experiment identity and exclusive lock for local/pilot executions."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path

from .config import REPO_ROOT
from .executor import CommandError


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def bind_run(ctx, stages=None):
    """Refuse stale cache reuse; changed inputs/config/code require a new experiment.

    The returned file owns the flock for the process lifetime. Existing cluster
    workflows are unchanged unless runtime.verify_cache is explicitly enabled.
    """
    if not ctx.cfg.get('runtime', {}).get('verify_cache', False):
        return None
    environments = None
    models = {}
    if ctx.cfg.get('runtime', {}).get('multi_image', False):
        environments = {'reconstruction': os.environ.get('PG_RECON_IMAGE_ID'),
                        'segmentation': os.environ.get('PG_SEG_IMAGE_ID')}
        role = os.environ.get('PG_RUN_ROLE', 'reconstruction')
        if role not in environments or not all(environments.values()) or environments[role] != os.environ.get('PG_IMAGE_ID'):
            raise CommandError('Actual image/role does not match declared reconstruction and segmentation images')
        if any((s == 'masks') != (role == 'segmentation') for s in (stages or [])):
            raise CommandError('Use segmentation image for masks only; reconstruction image for all other stages')
        if ctx.cfg.get('masking', {}).get('enabled'):
            from .model_assets import verify_manifest
            from .masks import backend_names, source_masking_cfg
            mcfg = ctx.cfg['masking']
            names = set(backend_names(mcfg))
            for source in mcfg.get('per_source', {}):
                names.update(backend_names(source_masking_cfg(mcfg, source)))
            paths = ctx.cfg.get('runtime', {}).get('model_manifests', {})
            for name in names & {'sam3', 'segformer'}:
                if name not in paths:
                    raise CommandError(f'Missing model manifest for {name}')
                models[name] = verify_manifest(paths[name])
                root = Path(paths[name]).resolve().parent
                configs = [mcfg] + [source_masking_cfg(mcfg, source) for source in mcfg.get('per_source', {})]
                for source_cfg in configs:
                    if name not in backend_names(source_cfg):
                        continue
                    backend = (source_cfg.get('backends') or {}).get(name) or {}
                    value = backend.get('checkpoint' if name == 'sam3' else 'model_id')
                    expected = Path(value).resolve() if value else None
                    if expected is None or (expected.parent if name == 'sam3' else expected) != root:
                        raise CommandError(f'{name} must load the local assets covered by its declared manifest')
                    if name == 'sam3' and expected.name not in json.loads(Path(paths[name]).read_text())['files']:
                        raise CommandError('SAM 3 checkpoint is not covered by its model manifest')
    lock = (ctx.out_dir / '.run.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise CommandError('Otro proceso está utilizando este experimento') from exc
    inputs = {str(p.relative_to(ctx.raw_dir)): digest(p)
              for p in sorted(ctx.raw_dir.rglob('*'))
              if p.is_file() and not any(x.startswith('.') for x in p.relative_to(ctx.raw_dir).parts)
              and not p.name.endswith('.part')}
    code = {str(p.relative_to(REPO_ROOT)): digest(p) for p in sorted((REPO_ROOT / 'pipeline').rglob('*.py'))}
    identity = {'schema': 1, 'scene': ctx.scene, 'config': ctx.cfg,
                 'inputs_sha256': inputs, 'code_sha256': code,
                 'image_id': os.environ.get('PG_IMAGE_ID')}
    if environments:
        identity.pop('image_id')
        identity.update(schema=2, environments=environments, models=models)
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    manifest = ctx.out_dir / 'run_manifest.json'
    if manifest.exists():
        if json.loads(manifest.read_text()).get('signature') != signature:
            lock.close()
            raise CommandError('Cambiaron datos, configuración, código o imagen. Usar un experiment.name nuevo; '
                               'no se reutilizarán marcadores potencialmente obsoletos.')
    else:
        if any(ctx.markers_dir.glob('*.done')):
            lock.close()
            raise CommandError('Hay marcadores antiguos sin procedencia. Usar un experiment.name nuevo.')
        identity['signature'] = signature
        temp = manifest.with_suffix('.json.tmp')
        temp.write_text(json.dumps(identity, indent=2, ensure_ascii=False) + '\n')
        temp.replace(manifest)
    # Keep downstream markers from referring to outputs that --force will replace.
    return lock

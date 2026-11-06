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


def bind_run(ctx):
    """Refuse stale cache reuse; changed inputs/config/code require a new experiment.

    The returned file owns the flock for the process lifetime. Existing cluster
    workflows are unchanged unless runtime.verify_cache is explicitly enabled.
    """
    if not ctx.cfg.get('runtime', {}).get('verify_cache', False):
        return None
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

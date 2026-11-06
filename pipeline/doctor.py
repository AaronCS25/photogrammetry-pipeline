"""Read-only diagnostics inside the reconstruction container."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .config import load_config, make_context


def run_doctor(args) -> int:
    checks = []

    def check(name, ok, detail):
        checks.append({'check': name, 'ok': bool(ok), 'detail': str(detail)})

    cfg = load_config(args.config)
    data_root = Path(os.environ.get('PG_DATA_ROOT', '/work'))
    check('data_directory', data_root.is_dir(), data_root)
    if data_root.is_dir():
        free = shutil.disk_usage(data_root).free / 1024**3
        minimum = float(os.environ.get('PG_MIN_FREE_GIB', '20'))
        check('free_space', free >= minimum, f'{free:.1f} GiB libres; mínimo {minimum:g}')
    for name in ('yaml', 'numpy', 'PIL', 'pycolmap'):
        try:
            module = importlib.import_module(name)
            check(name, True, getattr(module, '__version__', 'import OK'))
            if name == 'pycolmap':
                check('pycolmap_cuda', module.has_cuda, module.has_cuda)
                check('pycolmap_version', module.__version__ == '3.11.1', module.__version__)
        except (ImportError, OSError) as exc:
            check(name, False, exc)
    binaries = ['ffmpeg', 'ffprobe', 'colmap']
    if args.profile == 'dense':
        binaries += ['InterfaceCOLMAP', 'DensifyPointCloud']
    for binary in binaries:
        path = shutil.which(binary)
        check(binary, path is not None, path or 'not found')
    try:
        result = subprocess.run(['colmap', 'help'], text=True, capture_output=True, timeout=30)
        text = result.stdout + result.stderr
        check('colmap_version', result.returncode == 0 and 'COLMAP 3.11.1' in text and 'with CUDA' in text,
              next((s for s in text.splitlines() if 'COLMAP ' in s), text[-500:]))
    except (OSError, subprocess.TimeoutExpired) as exc:
        check('colmap_version', False, exc)
    if cfg.get('runtime', {}).get('gpu', True):
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=name,driver_version,memory.free,compute_cap',
                                     '--format=csv,noheader'], text=True, capture_output=True, timeout=30)
            check('gpu_access', result.returncode == 0, result.stdout.strip() or result.stderr.strip())
        except (OSError, subprocess.TimeoutExpired) as exc:
            check('gpu_access', False, exc)
    if args.scene:
        try:
            ctx = make_context(cfg, args.scene)
            from .frames import discover_sources
            sources = discover_sources(ctx.raw_dir)
            check('scene_media', bool(sources), ctx.raw_dir)
            ancestor = ctx.out_dir
            while not ancestor.exists():
                ancestor = ancestor.parent
            check('output_writable', os.access(ancestor, os.W_OK), ancestor)
            if os.environ.get('PG_DATA_ROOT'):
                check('output_on_data_volume', ctx.out_dir.resolve().is_relative_to(data_root.resolve()), ctx.out_dir)
        except Exception as exc:
            check('scene', False, exc)
    tag = data_root / 'NODE_TAG.txt'
    expected = os.environ.get('PG_NODE_TAG', 'local')
    if tag.is_file() and expected != 'local':
        check('node_tag', tag.read_text().strip() == expected, expected)
    report = {'ok': all(c['ok'] for c in checks), 'profile': args.profile,
              'image_id': os.environ.get('PG_IMAGE_ID'), 'node_tag': expected, 'checks': checks}
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for item in checks:
            print(f"{'OK' if item['ok'] else 'FAIL':4} {item['check']}: {item['detail']}")
    return 0 if report['ok'] else 1

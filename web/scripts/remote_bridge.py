"""Se ejecuta en el nodo maestro de Khipu vía `ssh khipu python3 -` (stdin).

Fase 1: SOLO LECTURA. Lista escenas (datasets/raw), experimentos (outputs),
etapas hechas, métricas resumidas y jobs `photogram` propios. No escribe nada,
no lanza nada y no ejecuta git. Solo biblioteca estándar (el conda del maestro
no tiene PyYAML ni PIL). Todo identificador que llega del cliente se valida.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(os.environ.get('PHOTOGRAM_ROOT') or (Path.home() / 'photogrammetry-pipeline'))
STAGES = ['frames', 'telemetry', 'masks', 'sfm', 'georef', 'undistort', 'dense', 'mesh', 'texture', 'metrics']
RESERVED = {'mask_overrides', 'mascaras'}
MEDIA = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.dng', '.mp4', '.mov', '.avi', '.mkv'}
NOTICE = re.compile(r'\[pipeline\] ERROR|ERROR de configuración|Traceback|ADVERTENCIA|AVISO|Error:|error:|Killed|oom|CANCELLED|DUE TO TIME LIMIT')
TERMINAL = {'COMPLETED', 'FAILED', 'OUT_OF_MEMORY', 'TIMEOUT', 'CANCELLED', 'NODE_FAIL', 'PREEMPTED', 'BOOT_FAIL', 'DEADLINE'}


def identifier(value, pattern=r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}'):
    value = str(value)
    if not re.fullmatch(pattern, value) or '..' in value:
        raise ValueError('Identificador no válido')
    return value


def safe(relative):
    path = (ROOT / relative).resolve()
    root = str(ROOT.resolve())
    if os.path.commonpath([str(path), root]) != root:
        raise ValueError('Ruta fuera del proyecto')
    return path


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def command(args, timeout=30):
    return subprocess.run(args, cwd=str(ROOT), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=timeout).stdout.strip()


def visible(path):
    return not path.name.startswith(('_', '.'))


def sources(scene_dir):
    """Subcarpetas fuente de una escena (sin las reservadas) con nº de archivos y tamaño."""
    items = []
    for sub in sorted(p for p in scene_dir.iterdir() if p.is_dir() and visible(p) and p.name not in RESERVED):
        count, size, kinds = 0, 0, {}
        for f in sub.iterdir():
            if f.is_file() and visible(f):
                count += 1
                size += f.stat().st_size
                ext = f.suffix.lower()
                if ext in MEDIA:
                    kinds[ext] = kinds.get(ext, 0) + 1
        items.append({'name': sub.name, 'files': count, 'bytes': size, 'kinds': kinds})
    return items


def scene_info(scene_dir):
    info = {'name': scene_dir.name, 'sources': sources(scene_dir),
            'mtime': int(scene_dir.stat().st_mtime), 'files': sorted(p.name for p in scene_dir.iterdir() if p.is_file())[:20]}
    ident = read_json(scene_dir / 'identificacion.json')
    if isinstance(ident, dict) and re.fullmatch(r'(MZ|LT)-BAR-[0-9A-F]{10}', str(ident.get('code', ''))):
        info['identification'] = {k: ident.get(k) for k in ('code', 'kind', 'name', 'center', 'photos', 'capture_dates')}
    else:
        # Exports subidos sin identificacion.json: el manifest trae los lotes del objetivo.
        manifest = read_json(scene_dir / 'manifest.json')
        target = ((manifest or {}).get('plan') or {}).get('target') if isinstance(manifest, dict) else None
        if isinstance(target, dict) and target.get('scope') in ('block', 'lot') and target.get('lot_ids'):
            info['identification'] = {'code': target_code(target['scope'], target['lot_ids']), 'kind': target['scope'],
                                      'name': manifest.get('name'), 'center': target.get('center'),
                                      'photos': len(manifest.get('views') or [])}
    return info


def target_code(scope, lot_ids):
    """Igual que barranco-streetview/identification.py: id de manzana/lote del proyecto."""
    ids = sorted(set(str(v) for v in lot_ids))
    digest = hashlib.sha256(json.dumps(['barranco-gis-v1', scope, ids], separators=(',', ':')).encode()).hexdigest()[:10].upper()
    return ('MZ' if scope == 'block' else 'LT') + '-BAR-' + digest


def summarize_metrics(m):
    """Subconjunto de metrics.json que la UI muestra en listas (el resto, bajo demanda)."""
    if not isinstance(m, dict):
        return None
    sparse = m.get('sparse') or {}
    georef = m.get('georef') or {}
    masking = m.get('masking') or {}
    texture = (m.get('artifacts') or {}).get('texture_obj')
    return {
        'generated_at': m.get('generated_at'), 'notes': m.get('notes'), 'dense_backend': m.get('dense_backend'),
        'frames_total': m.get('frames_total'), 'frames_per_source': m.get('frames_per_source'),
        'registered_images': sparse.get('registered_images'), 'registration_ratio': sparse.get('registration_ratio'),
        'model': sparse.get('model'), 'points3d': sparse.get('points3d'),
        'reprojection_px': sparse.get('mean_reprojection_error_px'), 'sparse_error': sparse.get('error'),
        'timings_total_seconds': m.get('timings_total_seconds'), 'timings_seconds': m.get('timings_seconds'),
        'georef': {k: georef.get(k) for k in ('cameras', 'enu_origin_gps', 'roi') if k in georef} or None,
        'mean_masked_ratio': masking.get('mean_masked_ratio'), 'mask_backends': masking.get('backends'),
        'job': (m.get('environment') or {}).get('slurm_job_id'),
        'texture': texture,
    }


def experiment_info(exp_dir):
    stages = {}
    for marker in (exp_dir / '.stages').glob('*.done'):
        data = read_json(marker) or {}
        stages[marker.stem] = {'at': data.get('finished_at'), 'seconds': data.get('elapsed_seconds'),
                               'job': data.get('slurm_job_id'), 'host': data.get('hostname')}
    georef = read_json(exp_dir / 'metrics' / 'georef_info.json') or {}
    texture = exp_dir / 'mvs' / 'scene_texture.obj'
    sparse = exp_dir / 'colmap' / 'sparse'
    return {
        'fragments': sum(1 for p in sparse.iterdir() if p.is_dir() and p.name.isdigit()) if sparse.is_dir() else None,
        'name': exp_dir.name, 'mtime': int(exp_dir.stat().st_mtime), 'stages': stages,
        'jobs': sorted({s['job'] for s in stages.values() if s.get('job')}, key=lambda j: int(j) if j.isdigit() else 0),
        'metrics': summarize_metrics(read_json(exp_dir / 'metrics' / 'metrics.json')),
        'origin': georef.get('enu_origin_gps'),
        'textured': texture.is_file(),
        'masks': [p.name for p in (exp_dir / 'masks').iterdir() if p.is_dir()] if (exp_dir / 'masks').is_dir() else [],
    }


def log_path(job):
    return safe('slurm-logs/photogram-' + identifier(job, r'[0-9]{1,12}') + '.out')


def log_header(job):
    """Primera línea del log: '== Job <id> en <host> | config=<yaml> --scene <escena> ...'."""
    path = log_path(job)
    if not path.is_file():
        return {}
    with path.open(encoding='utf-8', errors='replace') as stream:
        first = stream.readline().strip()
    match = re.search(r'config=(\S+)(?:\s+--scene\s+(\S+))?(.*)$', first)
    if not match:
        return {}
    config, scene, extra = match.group(1), match.group(2), match.group(3).strip()
    experiment = None
    try:
        text = safe(config).read_text(encoding='utf-8')
        found = re.search(r'^experiment:\s*\n(?:[ \t]+.*\n)*?[ \t]+name:\s*["\']?([^"\'#\s]+)', text, re.M)
        experiment = found.group(1) if found else None
    except (OSError, ValueError):
        pass
    return {'config': config, 'scene': scene, 'experiment': experiment, 'args': extra}


def notices(job, limit=8):
    path = log_path(job)
    if not path.is_file():
        return []
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[-400:]
    return [l[:300] for l in lines if NOTICE.search(l)][-limit:]


def jobs(days=14):
    """Jobs `photogram` propios: activos (squeue) y recientes (sacct)."""
    found = {}
    try:
        rows = command(['sacct', '-X', '-n', '-P', '--name=photogram', '-S', 'now-%ddays' % days,
                        '--format=JobID,State,Elapsed,ExitCode,Submit,Start,End,NodeList,ReqMem,AllocTRES'])
        for line in rows.splitlines():
            p = line.split('|')
            if len(p) >= 10 and p[0].isdigit():
                found[p[0]] = {'id': p[0], 'state': p[1].split()[0], 'elapsed': p[2], 'exit': p[3], 'submit': p[4],
                               'start': p[5], 'end': p[6], 'node': p[7], 'mem': p[8], 'tres': p[9]}
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        rows = command(['squeue', '--me', '-h', '--name=photogram', '-o', '%i|%T|%r|%M|%N|%b'])
        for line in rows.splitlines():
            p = line.split('|')
            if len(p) >= 6 and p[0].isdigit():
                found.setdefault(p[0], {'id': p[0]}).update(state=p[1], reason=p[2], elapsed=p[3], node=p[4], gres=p[5], active=True)
    except (OSError, subprocess.SubprocessError):
        pass
    for job in found.values():
        job.update(log_header(job['id']))
        if job.get('state') not in ('COMPLETED', 'RUNNING', 'PENDING'):
            job['notices'] = notices(job['id'], 4)
    return sorted(found.values(), key=lambda j: int(j['id']), reverse=True)


def listing(req):
    raw, outputs = ROOT / 'datasets' / 'raw', ROOT / 'outputs'
    scenes = {}
    if raw.is_dir():
        for d in sorted(p for p in raw.iterdir() if p.is_dir() and visible(p)):
            scenes[d.name] = scene_info(d)
    if outputs.is_dir():
        for d in sorted(p for p in outputs.iterdir() if p.is_dir() and visible(p)):
            entry = scenes.setdefault(d.name, {'name': d.name, 'sources': None, 'missingRaw': True})
            entry['experiments'] = [experiment_info(e) for e in sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                                    if e.is_dir() and visible(e)]
    return {'root': str(ROOT), 'scenes': sorted(scenes.values(), key=lambda s: s['name']),
            'jobs': jobs(int(req.get('days', 14)))}


def metrics(req):
    scene, exp = identifier(req['scene']), identifier(req['experiment'])
    base = safe('outputs/%s/%s' % (scene, exp))
    logs = base / 'logs'
    return {'metrics': read_json(base / 'metrics' / 'metrics.json'),
            'georef': read_json(base / 'metrics' / 'georef_info.json'),
            'logs': sorted(p.name for p in logs.iterdir() if p.is_file()) if logs.is_dir() else []}


def log(req):
    job = identifier(req['job'], r'[0-9]{1,12}')
    path = log_path(job)
    if not path.is_file():
        raise ValueError('No existe slurm-logs/photogram-%s.out' % job)
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    tail = lines[-min(int(req.get('lines', 200)), 2000):]
    if req.get('filtered'):
        tail = [l for l in lines if l.startswith(('[', '==')) or NOTICE.search(l)][-200:]
    return {'job': job, 'total': len(lines), 'lines': [l[:500] for l in tail]}


def dispatch(req):
    op = req.get('op')
    if op == 'health':
        checks = {p: (ROOT / p).exists() for p in ('slurm/submit.sh', 'containers/photogrammetry.sif',
                                                    'containers/segmentation.sif', 'datasets/raw', 'outputs')}
        return {'root': str(ROOT), 'checks': checks, 'ready': ROOT.is_dir() and checks['slurm/submit.sh']}
    if op == 'list':
        return listing(req)
    if op == 'metrics':
        return metrics(req)
    if op == 'log':
        return log(req)
    raise ValueError('Operación no soportada')

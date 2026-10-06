"""Se ejecuta en el nodo maestro de Khipu vía `ssh khipu python3 -` (stdin).

Lectura: escenas (datasets/raw), experimentos (outputs), etapas hechas,
métricas resumidas y jobs `photogram` propios.
Escritura (fase 2), siempre dentro del repo: YAML del experimento en
datasets/raw/<escena>/_ui/ (carpeta ignorada por el pipeline por el prefijo
`_`), recibos de envío en _ui/requests/, copia de un experimento para
reutilizar etapas, `slurm/submit.sh` y `scancel` de jobs `photogram` propios.
Nunca ejecuta git. Solo biblioteca estándar (el conda del maestro no tiene
PyYAML ni PIL). Todo identificador que llega del cliente se valida.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

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
    ui_config = ROOT / 'datasets' / 'raw' / exp_dir.parent.name / '_ui' / (exp_dir.name + '.yaml')
    return {
        'uiConfig': ui_config.is_file(),
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


def progress(job):
    """Etapa en curso y última línea de progreso de un job en ejecución (cola del log)."""
    path = log_path(job)
    if not path.is_file():
        return {}
    with path.open('rb') as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 65536))
        lines = stream.read().decode('utf-8', errors='replace').splitlines()
    stage = last = None
    for line in lines:
        found = re.match(r"\[pipeline\] etapa '(\w+)': iniciando", line)
        if found:
            stage = found.group(1)
        if line.startswith('['):
            last = line[:200]
    return {'stage': stage, 'last': last}


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
        rows = command(['squeue', '--me', '-h', '--name=photogram', '-o', '%i|%T|%r|%M|%N|%b|%m|%k'])
        for line in rows.splitlines():
            p = line.split('|')
            if len(p) >= 8 and p[0].isdigit():
                found.setdefault(p[0], {'id': p[0]}).update(state=p[1], reason=p[2], elapsed=p[3], node=p[4], gres=p[5],
                                                            reqmem=p[6], comment=p[7], active=True)
    except (OSError, subprocess.SubprocessError):
        pass
    for job in found.values():
        job.update(log_header(job['id']))
        if job.get('state') not in ('COMPLETED', 'RUNNING', 'PENDING'):
            job['notices'] = notices(job['id'], 4)
        if job.get('state') == 'RUNNING':
            job.update(progress(job['id']))
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


# ---------------------------------------------------------------- escritura (fase 2)
NAME = r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}'
SBATCH = [r'--gres=(shard:(tesla|rtxa6000|a100):\d{1,2}|shard:\d{1,2}|gpu:(tesla:|rtxa6000:|a100:)?1)',
          r'--cpus-per-task=\d{1,3}', r'--mem=\d{1,4}[GM]', r'--time=(\d{1,2}-)?\d{1,2}:\d{2}:\d{2}',
          r'--partition=[a-z-]{1,20}', r'--(nodelist|exclude)=[a-z0-9,]{1,60}']


def raw_dir(scene):
    return safe('datasets/raw/' + identifier(scene, NAME))


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_bytes(data if isinstance(data, bytes) else json.dumps(data, indent=1).encode('utf-8'))
    temp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_path(value, scene):
    """Solo YAML del repo (configs/experiments/) o de la carpeta _ui/ de la propia escena."""
    value = str(value)
    if not (re.fullmatch(r'configs/experiments/[a-zA-Z0-9_.-]+\.yaml', value)
            or value == 'datasets/raw/%s/_ui/%s' % (scene, value.rsplit('/', 1)[-1])
            and re.fullmatch(NAME + r'\.yaml', value.rsplit('/', 1)[-1])):
        raise ValueError('Configuración no permitida: %s' % value)
    if not safe(value).is_file():
        raise ValueError('No existe %s en Khipu' % value)
    return value


def check_args(args):
    """Argumentos extra del pipeline: --from-stage X, --stages a,b y --force."""
    out, items = [], list(args or [])
    while items:
        token = items.pop(0)
        if token == '--force':
            out.append(token)
        elif token in ('--from-stage', '--stages') and items:
            value = items.pop(0)
            if not all(v in STAGES for v in value.split(',')) or (token == '--from-stage' and ',' in value):
                raise ValueError('Etapa no válida: %s' % value)
            out += [token, value]
        else:
            raise ValueError('Argumento no permitido: %s' % token)
    return out


def ls(req):
    """Archivos ya subidos de una escena (ruta relativa -> tamaño), sin carpetas reservadas `_`/`.`."""
    base = raw_dir(req['scene'])
    if not base.is_dir():
        return {'exists': False, 'files': {}}
    files = {}
    for path in base.rglob('*'):
        rel = path.relative_to(base)
        if path.is_file() and not any(part.startswith(('_', '.')) for part in rel.parts):
            files[rel.as_posix()] = path.stat().st_size
    return {'exists': True, 'files': files}


def validate(req):
    """Escribe (opcional) _ui/<exp>.yaml y ejecuta `pipeline validate` en el contenedor (nodo maestro, sin GPU)."""
    scene = identifier(req['scene'], NAME)
    if req.get('yaml') is not None:
        name = identifier(req['experiment'], NAME) + '.yaml'
        atomic(raw_dir(scene) / '_ui' / name, base64.b64decode(req['yaml']))
        config = 'datasets/raw/%s/_ui/%s' % (scene, name)
    else:
        config = config_path(req['config'], scene)
    run = subprocess.run(['apptainer', 'exec', 'containers/photogrammetry.sif', 'python3', '-m', 'pipeline', 'validate',
                          '--config', config, '--scene', scene], cwd=str(ROOT), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, universal_newlines=True, timeout=240)
    output = run.stdout[-20000:]
    return {'ok': run.returncode == 0 and 'Configuración válida' in output, 'output': output, 'config': config,
            'sha256': digest(safe(config)), 'returncode': run.returncode}


def clone_experiment(scene, src, dst, from_stage):
    """Copia outputs/<escena>/<src> a <dst> conservando solo lo producido por las etapas anteriores a from_stage.

    Lo que rehacen las etapas siguientes se excluye (y sus marcadores .stages), igual que la limpieza
    manual documentada: mvs/, colmap/undistorted/, metrics/metrics.json...
    """
    i = STAGES.index(from_stage)
    if i == 0:
        raise ValueError('Desde "frames" no hay nada que reutilizar: lanza el experimento sin copiar.')
    redo = lambda stage: i <= STAGES.index(stage)
    excluded = {'metrics/metrics.json', 'config_resolved.yaml', 'metrics/timings.csv'}
    excluded |= {'.stages/%s.done' % s for s in STAGES[i:]}
    if redo('telemetry'):
        excluded.add('telemetry')
    if redo('masks'):
        excluded |= {'masks', 'metrics/masks_info.json'}
    if redo('sfm'):
        excluded |= {'colmap', 'metrics/sfm_timings.json', 'metrics/colmap_model_analyzer.txt'}
    if redo('georef'):
        excluded |= {'colmap/sparse_geo', 'colmap/sparse_aligned', 'colmap/roi.txt', 'colmap/georef_gps_refs.txt',
                     'colmap/georef_translation.txt', 'metrics/georef_info.json'}
    if redo('undistort'):
        excluded |= {'colmap/undistorted', 'mvs'}
    if redo('dense'):
        excluded.add('mvs')
    source = safe('outputs/%s/%s' % (scene, identifier(src)))
    target = safe('outputs/%s/%s' % (scene, identifier(dst, NAME)))
    if not source.is_dir():
        raise ValueError('No existe el experimento de origen %s' % src)
    if target.exists():
        raise ValueError('Ya existe outputs/%s/%s; elige otro nombre.' % (scene, dst))

    def ignore(folder, names):
        rel = Path(folder).relative_to(source)
        return {n for n in names if (rel / n).as_posix() in excluded}
    shutil.copytree(str(source), str(target), symlinks=True, ignore=ignore)
    return sorted(excluded)


def active_jobs():
    rows = command(['squeue', '--me', '-h', '--name=photogram', '-o', '%i|%k'])
    return dict(line.split('|', 1) for line in rows.splitlines() if '|' in line)


def reconcile(receipt, path):
    """Envío interrumpido: busca el job por comentario (activo) o por log tras la hora del recibo."""
    for job, comment in active_jobs().items():
        if comment == 'studio:' + receipt['id']:
            receipt.update(job=job, status='submitted', reconciled='comment')
            atomic(path, receipt)
            return receipt
    for log in sorted((ROOT / 'slurm-logs').glob('photogram-*.out')):
        if log.stat().st_mtime >= receipt['at'] - 5:
            job = log.stem.split('-')[1]
            header = log_header(job)
            if header.get('scene') == receipt['scene'] and header.get('config') == receipt['config']:
                receipt.update(job=job, status='submitted', reconciled='log')
                atomic(path, receipt)
                return receipt
    receipt['status'] = 'uncertain'
    atomic(path, receipt)
    return receipt


def submit(req):
    """`SBATCH_OPTS=... ./slurm/submit.sh <yaml> <escena> [args]` con recibo previo (idempotente por id)."""
    import fcntl
    rid = identifier(req['id'], r'[a-f0-9]{32}')
    scene = identifier(req['scene'], NAME)
    experiment = identifier(req['experiment'], NAME)
    config = config_path(req['config'], scene)
    tokens = [str(t) for t in req.get('sbatch') or []]
    for t in tokens:
        if not any(re.fullmatch(p, t) for p in SBATCH):
            raise ValueError('Opción de sbatch no permitida: %s' % t)
    args = check_args(req.get('args'))
    folder = raw_dir(scene) / '_ui' / 'requests'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (rid + '.json')
    with (folder / (rid + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = read_json(path)
        if receipt:
            if receipt.get('job') or receipt.get('status') == 'error':
                return receipt
            return reconcile(receipt, path)
        if req.get('sha256') and digest(safe(config)) != req['sha256']:
            raise ValueError('El YAML en Khipu cambió desde la validación. Vuelve a validar.')
        active = active_jobs()
        for other in folder.glob('*.json'):
            data = read_json(other) or {}
            if data.get('experiment') == experiment and data.get('job') in active:
                raise ValueError('Ya hay un job activo (%s) para %s.' % (data['job'], experiment))
        for job in active:
            if log_header(job).get('scene') == scene and log_header(job).get('experiment') == experiment:
                raise ValueError('Ya hay un job activo (%s) para %s.' % (job, experiment))
        receipt = {'id': rid, 'scene': scene, 'experiment': experiment, 'config': config, 'sbatch': tokens,
                   'args': args, 'at': time.time(), 'status': 'submitting'}
        clone = req.get('clone')
        if clone:
            receipt['clone'] = {'from': identifier(clone['from']), 'fromStage': clone['fromStage']}
            if clone['fromStage'] not in STAGES:
                raise ValueError('Etapa no válida')
            receipt['status'] = 'cloning'
            atomic(path, receipt)
            receipt['clone']['excluded'] = clone_experiment(scene, clone['from'], experiment, clone['fromStage'])
            receipt['status'] = 'submitting'
        atomic(path, receipt)
        env = dict(os.environ, SBATCH_OPTS=' '.join(tokens + ['--comment=studio:' + rid]))
        run = subprocess.run(['./slurm/submit.sh', config, scene] + args, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True, timeout=90)
        found = re.search(r'Submitted batch job (\d+)', run.stdout)
        if run.returncode != 0 or not found:
            receipt.update(status='error', error=(run.stderr or run.stdout).strip()[-2000:])
            atomic(path, receipt)
            raise ValueError('sbatch falló: ' + receipt['error'])
        receipt.update(job=found.group(1), status='submitted', submittedAt=time.time())
        atomic(path, receipt)
        return receipt


def cancel(req):
    job = identifier(req['job'], r'[0-9]{1,12}')
    try:
        name = command(['squeue', '--me', '-h', '-j', job, '-o', '%j'])
    except subprocess.CalledProcessError:
        name = ''
    if name.strip() != 'photogram':
        raise ValueError('El job %s no es un job photogram activo tuyo (¿ya terminó?).' % job)
    command(['scancel', job])
    return {'job': job, 'cancelled': True}


def read_config(req):
    """YAML de un experimento existente para clonarlo: el de _ui/ si lo creó la app, si no config_resolved.yaml."""
    scene, exp = identifier(req['scene']), identifier(req['experiment'])
    ui = ROOT / 'datasets' / 'raw' / scene / '_ui' / (exp + '.yaml')
    if ui.is_file():
        return {'kind': 'ui', 'path': str(ui.relative_to(ROOT)), 'text': ui.read_text(encoding='utf-8')}
    resolved = safe('outputs/%s/%s/config_resolved.yaml' % (scene, exp))
    if resolved.is_file():
        return {'kind': 'resolved', 'path': str(resolved.relative_to(ROOT)), 'text': resolved.read_text(encoding='utf-8')}
    raise ValueError('No hay YAML guardado para %s/%s' % (scene, exp))


def dispatch(req):
    op = req.get('op')
    if op in ('ls', 'validate', 'submit', 'cancel', 'read_config'):
        return globals()[op](req)
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

"""Additional commands; existing run/validate/report remain backwards compatible."""
import json
from pathlib import Path

from . import api
from .config import ConfigError, REPO_ROOT
from .state import read_json


def register(sub):
    p = sub.add_parser("doctor", help="Verificar versiones/CLI/binarios sin reconstruir")
    p.add_argument("--profile", default="barranco_sparse4k")
    p.add_argument("--target", choices=("sparse", "dense", "mesh", "textured"), default="sparse")
    p.set_defaults(func=doctor)
    p = sub.add_parser("execute-scene", help="Ejecutar sin scheduler dentro de Docker o una asignación Slurm")
    p.add_argument("--scene", required=True)
    p.add_argument("--request-id", required=True)
    p.add_argument("--profile", default="barranco_sparse4k")
    p.add_argument("--target", choices=("sparse", "dense", "mesh", "textured"), default="sparse")
    p.add_argument("--mode", choices=("update", "rebuild"), default="update")
    p.add_argument("--allow-partial", action="store_true")
    p.set_defaults(func=lambda a: dispatch("execute", a))
    for name in ("plan", "submit"):
        p = sub.add_parser(name, help=f"{name}: reconstrucción versionada por manzana")
        p.add_argument("--scene", required=True)
        p.add_argument("--profile", default="barranco_sparse4k")
        p.add_argument("--target", choices=("sparse", "dense", "mesh", "textured"), default="sparse")
        p.add_argument("--mode", choices=("update", "rebuild"), default="update")
        p.add_argument("--allow-partial", action="store_true")
        if name == "submit":
            p.add_argument("--request-id")
        p.set_defaults(func=lambda args, method=name: dispatch(method, args))
    p = sub.add_parser("status", help="Estado de versiones y jobs por manzana")
    p.add_argument("--scene", required=True)
    p.add_argument("--live", action="store_true", help="Consultar también squeue/sacct")
    p.set_defaults(func=lambda a: dispatch("status", a))
    for method in ("resume", "reconcile"):
        p = sub.add_parser(method, help=f"{method}: recuperar una solicitud existente")
        p.add_argument("--scene", required=True)
        p.add_argument("--request-id", required=True)
        p.set_defaults(func=lambda a, m=method: dispatch(m, a))
    p = sub.add_parser("submit-batch", help="Enviar manzanas independientes con concurrencia limitada")
    p.add_argument("--scenes", required=True, help="001,002,003")
    p.add_argument("--batch-id", required=True)
    p.add_argument("--max-active-scenes", type=int, default=2)
    p.add_argument("--profile", default="barranco_sparse4k")
    p.add_argument("--target", choices=("sparse", "dense", "mesh", "textured"), default="sparse")
    p.add_argument("--mode", choices=("update", "rebuild"), default="update")
    p.add_argument("--allow-partial", action="store_true")
    p.set_defaults(func=batch)
    p = sub.add_parser("scene-worker", help="Interno: ejecutar una fase dentro del contenedor")
    p.add_argument("--request", required=True)
    p.add_argument("--phase", choices=("prepare", "masks", "features", "matching", "mapping", "surface", "publish"), required=True)
    p.set_defaults(func=worker)


def dispatch(method, args):
    kw = {k: v for k, v in vars(args).items() if k not in ("func", "command")}
    result = getattr(api, method)(**kw)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if method != "plan" or result["ready"] else 2


def worker(args):
    from .workflow import execute_phase
    path = Path(args.request).resolve()
    allowed = (REPO_ROOT / "datasets/_state").resolve()
    if not path.is_relative_to(allowed):
        raise ConfigError("Solicitud fuera de datasets/_state")
    request = read_json(path)
    if not request:
        raise ConfigError("Solicitud no encontrada")
    execute_phase(REPO_ROOT, request, args.phase)
    return 0


def batch(args):
    kw = {k: v for k, v in vars(args).items() if k not in ("func", "command", "scenes")}
    result = api.submit_many(scenes=args.scenes.split(","), **kw)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def doctor(args):
    from .colmap.runtime import capabilities
    from .scenes import load_profile
    profile = load_profile(args.profile)
    print(json.dumps(capabilities(profile["colmap_version"], args.target), indent=2))
    return 0

"""Scene registration, independent from the physical location of raw media."""
from __future__ import annotations

import re
from pathlib import Path

from .config import ConfigError, REPO_ROOT, deep_merge, load_yaml

TARGETS = ("sparse", "dense", "mesh", "textured")


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ConfigError(f"Identificador no válido: {value!r}")
    return value


def resolve(root: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p.resolve() if p.is_absolute() else (root / p).resolve()


def load_scene(scene: str, root: Path = REPO_ROOT) -> dict:
    identifier(scene)
    direct = root / "configs/scenes" / f"{scene}.yaml"
    if direct.exists():
        cfg = load_yaml(direct)
    else:
        catalog = root / "configs/scenes/barranco.yaml"
        data = load_yaml(catalog) if catalog.exists() else {}
        registered = data.get("scenes", {})
        if scene in registered:
            cfg = deep_merge(data.get("defaults", {}), registered[scene] or {})
        else:
            cfg = {"raw_dir": f"datasets/raw/{scene}"}
    raw = cfg.get("raw_dir", f"datasets/raw/{scene}").replace("{scene}", scene)
    cfg.update(scene=scene, raw_dir=str(resolve(root, raw)))
    if cfg.get("legacy_root"):
        cfg["legacy_root"] = str(resolve(root, cfg["legacy_root"].replace("{scene}", scene)))
    return cfg


def load_profile(name: str, root: Path = REPO_ROOT, _parents: tuple = ()) -> dict:
    identifier(name)
    if name in _parents:
        raise ConfigError("Herencia circular de perfiles")
    cfg = load_yaml(root / "configs/profiles" / f"{name}.yaml")
    if "extends" in cfg:
        parent = cfg.pop("extends")
        cfg = deep_merge(load_profile(parent, root, (*_parents, name)), cfg)
    for key in ("colmap_version", "frames", "features", "matching", "mapping", "runtime", "resources"):
        if key not in cfg:
            raise ConfigError(f"Falta {key} en el perfil {name}")
    if cfg["frames"].get("fps", 0) <= 0:
        raise ConfigError("frames.fps debe ser positivo")
    for key in ("features", "matching", "mapping"):
        if not isinstance(cfg[key].get("threads"), int) or cfg[key]["threads"] < 1:
            raise ConfigError(f"{key}.threads debe ser un entero positivo")
    if cfg["frames"].get("format", "jpg") != "jpg":
        raise ConfigError("La ingesta versionada usa JPG; configurar frames.format: jpg")
    allowed_features = {"camera_model", "max_image_size", "max_num_features", "threads", "gpu", "extra_args"}
    if set(cfg["features"]) - allowed_features:
        raise ConfigError("Opciones de features desconocidas; usar extra_args para flags COLMAP")
    if cfg["matching"].get("overlap", 15) < 1:
        raise ConfigError("matching.overlap debe ser positivo")
    return cfg

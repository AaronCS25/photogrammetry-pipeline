"""Carga y merge de configuración YAML + resolución de rutas del experimento."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Raíz del repositorio (padre del paquete `pipeline`)
REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG = REPO_ROOT / "configs" / "default.yaml"


class ConfigError(RuntimeError):
    pass


def deep_merge(base: dict, override: dict) -> dict:
    """Merge recursivo: los dicts se combinan, escalares y listas se reemplazan."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"No existe el archivo de configuración: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"El YAML debe ser un mapeo en su raíz: {path}")
    return data


def load_config(experiment_config: str | None) -> dict:
    """default.yaml + (opcional) YAML de experimento, con merge profundo."""
    cfg = load_yaml(DEFAULT_CONFIG)
    if experiment_config:
        exp_path = Path(experiment_config)
        if not exp_path.is_absolute():
            exp_path = REPO_ROOT / exp_path
        cfg = deep_merge(cfg, load_yaml(exp_path))
    return cfg


def _resolve_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


@dataclass
class Context:
    """Rutas y estado resueltos para un run concreto (escena + experimento)."""

    cfg: dict
    scene: str
    experiment: str
    force: bool = False
    stages: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.raw_dir = _resolve_path(self.cfg["paths"]["raw_root"]) / self.scene
        self.out_dir = _resolve_path(self.cfg["paths"]["output_root"]) / self.scene / self.experiment
        self.frames_dir = self.out_dir / "frames"
        self.telemetry_dir = self.out_dir / "telemetry"
        self.colmap_dir = self.out_dir / "colmap"
        self.mvs_dir = self.out_dir / "mvs"
        self.metrics_dir = self.out_dir / "metrics"
        self.logs_dir = self.out_dir / "logs"
        self.markers_dir = self.out_dir / ".stages"

    @property
    def gpu(self) -> bool:
        return bool(self.cfg.get("runtime", {}).get("gpu", True))

    def prepare(self) -> None:
        for d in (self.out_dir, self.logs_dir, self.metrics_dir, self.markers_dir):
            d.mkdir(parents=True, exist_ok=True)

    def dump_resolved_config(self) -> None:
        resolved = copy.deepcopy(self.cfg)
        resolved["scene"] = self.scene
        with open(self.out_dir / "config_resolved.yaml", "w", encoding="utf-8") as fh:
            yaml.safe_dump(resolved, fh, sort_keys=False, allow_unicode=True)


def make_context(cfg: dict, scene: str | None, force: bool = False) -> Context:
    scene = scene or cfg.get("scene")
    if not scene:
        raise ConfigError("Falta la escena: usar --scene <nombre> o fijar 'scene:' en el YAML.")
    experiment = (cfg.get("experiment") or {}).get("name") or "default"
    ctx = Context(cfg=cfg, scene=str(scene), experiment=str(experiment), force=force)
    if not ctx.raw_dir.is_dir():
        raise ConfigError(
            f"No existe la escena '{scene}' en {ctx.raw_dir.parent} "
            f"(se esperaba la carpeta {ctx.raw_dir})"
        )
    return ctx

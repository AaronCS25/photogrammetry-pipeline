"""Pure planning: no writes, no jobs, no numerical work."""
from __future__ import annotations

from pathlib import Path

from .cache import asset_signature, feature_signature, legacy_allowed
from .config import ConfigError, REPO_ROOT
from .inventory import scan
from .scenes import TARGETS, load_profile, load_scene, resolve
from .state import code_signature, digest, read_json


def plan_scene(scene: str, profile: str = "barranco_sparse4k", target: str = "sparse",
               mode: str = "update", allow_partial: bool = False,
               root: Path = REPO_ROOT) -> dict:
    root = Path(root).resolve()
    if target not in TARGETS or mode not in ("update", "rebuild"):
        raise ConfigError("target o mode no válido")
    cfg, settings = load_scene(scene, root), load_profile(profile, root)
    if target not in settings.get("targets", TARGETS):
        raise ConfigError(f"El perfil {profile} no incluye el target {target}")
    if settings.get("matching", {}).get("vocab_tree_path"):
        settings["matching"]["vocab_tree_path"] = str(resolve(root, settings["matching"]["vocab_tree_path"]))
    if settings["colmap_version"] != "3.13.0":
        raise ConfigError("Escenas versionadas: solo COLMAP 3.13.0 está implementado; usar run para el flujo 3.11")
    backend = settings.get("pipeline", {}).get("dense", {}).get("backend", "openmvs")
    if backend == "colmap" and target == "textured":
        raise ConfigError("El backend COLMAP no texturiza; seleccionar OpenMVS")
    if backend == "colmap" and target == "mesh" and settings.get("pipeline", {}).get("dense", {}).get("colmap", {}).get("mesher", "none") == "none":
        raise ConfigError("Configurar dense.colmap.mesher para solicitar mesh")
    # Manual mask edits invalidate affected feature recipes even if YAML is unchanged.
    if settings.get("masking", {}).get("enabled"):
        manual = settings["masking"].get("backends", {}).get("manual", {}).get("dir", "mask_overrides")
        base = Path(manual)
        if not base.is_absolute():
            base = Path(cfg["raw_dir"]) / base
        settings["mask_inputs"] = [(str(p), p.stat().st_size, p.stat().st_mtime_ns)
                                   for p in sorted(base.rglob("*.png"))] if base.exists() else []
    inventory = scan(cfg)
    state = root / "datasets/_state" / scene
    previous = read_json(state / "last_result.json", {}) or read_json(state / "latest.json", {})
    old = read_json(root / previous["run"] / "run.json", {}) if previous else {}
    old_assets = {a["id"]: a for a in old.get("assets", [])}
    actions = []
    for item in inventory["items"]:
        key = asset_signature(item, settings)
        meta = root / "datasets/cache" / scene / "features" / key / "record.json"
        cached = read_json(meta, {})
        legacy = Path(cfg.get("legacy_root", "/nonexistent")) / Path(item["path"]).stem
        can_import = (item["kind"] == "video" and "/" not in item["relative"] and legacy_allowed(settings)
                      and (legacy / "database.db").is_file())
        action = "reuse" if cached else "import-legacy" if can_import else "extract"
        actions.append({**item, "cache_key": key, "action": action,
                        "legacy": str(legacy) if can_import else None})
    current_ids = {i["id"] for i in actions}
    removed = sorted(set(old_assets) - current_ids)
    changed = [a["relative"] for a in actions if a["id"] in old_assets
               and a["cache_key"] != old_assets[a["id"]]["cache_key"]]
    compatible = old.get("feature_signature") == feature_signature(settings)
    incremental = bool(previous and compatible and not removed and not changed and mode == "update")
    # Changing matcher settings invalidates old verified matches, not SIFT.
    reuse_matches = incremental and old.get("matching") == settings["matching"]
    code = code_signature(root)
    reuse_sparse = bool(reuse_matches and old.get("mapping") == settings["mapping"]
                        and old.get("code_signature") == code
                        and current_ids == set(old_assets))
    snapshot = digest({"scene": cfg, "profile": settings, "items": actions,
                       "errors": inventory["errors"], "target": target, "mode": mode,
                       "allow_partial": allow_partial, "parent": previous, "code": code})
    errors = inventory["errors"]
    image = resolve(root, settings["runtime"].get("image", "containers/photogrammetry313.sif"))
    return {"schema": 1, "scene": scene, "profile_name": profile, "profile": settings,
            "code_signature": code,
            "scene_config": cfg, "target": target, "mode": mode,
            "allow_partial": allow_partial, "assets": actions, "errors": errors,
            "ready": bool(actions) and (not errors or allow_partial),
            "deployment": {"image": str(image), "image_exists": image.is_file(),
                           "expected_colmap": settings["colmap_version"],
                           "verification": "Ejecutar doctor dentro de la imagen antes de usarla"},
            "feature_signature": feature_signature(settings), "snapshot": snapshot,
            "parent": previous or None, "incremental": incremental,
            "reuse_matches": reuse_matches, "reuse_sparse": reuse_sparse,
            "removed": removed, "changed": changed}

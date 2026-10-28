"""Versioned execution. Invoked inside a worker/container, not at submission."""
from __future__ import annotations

import os
import re
import shutil
import time
import platform
from datetime import datetime, timezone
from pathlib import Path

from . import clean, frames, georef, masks, mvs, sfm, telemetry
from .cache import legacy_allowed
from .colmap.database import backup, consolidate
from .colmap.mapping import run_mapping
from .colmap.matching import run_matching
from .colmap.runtime import general_flags, require_version
from .config import ConfigError, Context, deep_merge, load_yaml
from .executor import run_cmd
from .inventory import scan
from .metrics import run_metrics
from .state import atomic_json, code_signature, digest, file_hash, lock, read_json
from .validation import feature_database, image_dimensions, probe_video, readonly


def context(root: Path, request: dict) -> Context:
    cfg = deep_merge(load_yaml(root / "configs/default.yaml"), request["profile"].get("pipeline", {}))
    cfg["masking"] = deep_merge(cfg["masking"], request["profile"].get("masking", {}))
    cfg["frames"] = deep_merge(cfg["frames"], request["profile"]["frames"])
    cfg["colmap"]["camera_model"] = request["profile"]["features"]["camera_model"]
    cfg["versioned_profile"] = request["profile"]
    cfg["experiment"] = {"name": request["experiment"], "notes": "Versioned scene pipeline"}
    cfg["paths"]["output_root"] = str(root / "outputs")
    cfg["runtime"]["gpu"] = request["profile"]["matching"].get("gpu", False)
    ctx = Context(cfg, request["scene"], request["experiment"])
    ctx.raw_dir = Path(request["scene_config"]["raw_dir"])
    return ctx


def _link(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink():
        if dst.resolve() == src.resolve():
            return
        dst.unlink()
    elif dst.exists():
        raise ConfigError(f"No se sobrescribe imagen existente: {dst}")
    dst.symlink_to(src.resolve())


def prepare(root: Path, request: dict, ctx: Context):
    """Materialize immutable frame cache; compute full raw hashes on compute node."""
    current = scan(request["scene_config"])
    planned = [(a["id"], a["stamp"]) for a in request["assets"]]
    if planned != [(a["id"], a["stamp"]) for a in current["items"]] or current["errors"] != request["errors"]:
        raise ConfigError("Los datos cambiaron después del plan: crear una nueva solicitud")
    if [a.get("sidecars", []) for a in current["items"]] != [a.get("sidecars", []) for a in request["assets"]]:
        raise ConfigError("La telemetría cambió después del plan")
    for path, size, mtime in request["profile"].get("mask_inputs", []):
        p = Path(path)
        if not p.is_file() or (p.stat().st_size, p.stat().st_mtime_ns) != (size, mtime):
            raise ConfigError("Las máscaras manuales cambiaron después del plan")
    assets = []
    for item in request["assets"]:
        source = Path(item["path"])
        sha = file_hash(source)
        st = source.stat()
        if {"size": st.st_size, "mtime_ns": st.st_mtime_ns} != item["stamp"]:
            raise ConfigError(f"El archivo cambió durante la lectura: {source}")
        frame_key = digest({"sha256": sha, "frames": request["profile"]["frames"], "kind": item["kind"]})
        cache = root / "datasets/cache" / request["scene"]
        frame_dir = cache / "frames" / frame_key
        frame_record = frame_dir / "record.json"
        provenance = "extracted"
        if item["kind"] == "video":
            media = probe_video(source)
        else:
            media = {"size": image_dimensions(source)}
        with lock(cache / "locks" / f"frames-{frame_key}.lock"):
            record = read_json(frame_record, {})
            if not record:
                temp = frame_dir.with_name(frame_key + ".partial")
                if temp.exists():
                    shutil.rmtree(temp)
                temp.mkdir(parents=True)
                legacy = Path(item["legacy"]) if item.get("legacy") else None
                if legacy and legacy_allowed(request["profile"]):
                    names = {p.name for p in (legacy / "frames").glob("*.jpg")}
                    feature_database(legacy / "database.db", names)
                    with readonly(legacy / "database.db") as db:
                        cameras = db.execute("SELECT model,width,height FROM cameras").fetchall()
                    if not cameras or any(tuple(c) != (2, 3840, 2160) for c in cameras):
                        raise ConfigError(f"Modelo de cámara legacy incompatible: {legacy}")
                    duration = float(media["streams"][0].get("duration") or media.get("format", {}).get("duration", 0))
                    if duration and abs(len(names) - duration * request["profile"]["frames"]["fps"]) > 2:
                        raise ConfigError(f"Frames legacy incompletos: {legacy}")
                    for name in sorted(names):
                        src = legacy / "frames" / name
                        if image_dimensions(src) != (3840, 2160):
                            raise ConfigError(f"Legacy no es 4K: {src}")
                        shutil.copy2(src, temp / name)
                    provenance = "legacy-adoption"
                elif item["kind"] == "video":
                    run_cmd(frames.build_ffmpeg_cmd(source, temp / "f%06d.jpg", request["profile"]["frames"]),
                            ctx.logs_dir / "frames.log")
                else:
                    settings = request["profile"]["frames"]
                    if settings.get("resize"):
                        raise ConfigError("Fotos: usar resize:null para conservar EXIF")
                    shutil.copy2(source, temp / ("photo" + source.suffix.lower()))
                files = sorted(p for p in temp.iterdir() if p.suffix.lower() in frames.IMAGE_EXTS)
                if not files:
                    raise ConfigError(f"No hay imágenes de {source}")
                sizes = {image_dimensions(p) for p in files}
                record = {"sha256": sha, "names": [p.name for p in files],
                          "sizes": sorted(sizes), "provenance": provenance, "probe": media}
                atomic_json(temp / "record.json", record)
                temp.rename(frame_dir)
            if record["sha256"] != sha:
                raise ConfigError("Identidad de caché inconsistente")
            for name in record["names"]:
                image_dimensions(frame_dir / name)
        images = []
        from .srt_positions import seconds
        offset = seconds(request["profile"]["frames"].get("start") or "0")
        for index, name in enumerate(record["names"]):
            # Stable globally unique basenames also satisfy OpenMVS mask aliases.
            relative = f"{item['source']}/{item['id']}_{name}"
            _link(frame_dir / name, ctx.frames_dir / relative)
            match = re.search(r"(\d+)(?=\.[^.]+$)", name)
            number = int(match.group(1)) if match else index + 1
            images.append({"name": name, "relative": relative, "index": number,
                           "time_s": offset + index / request["profile"]["frames"]["fps"] if item["kind"] == "video" else None,
                           "asset": item["id"], "kind": item["kind"]})
        assets.append({**item, "sha256": sha, "frame_dir": str(frame_dir), "images": images,
                       "frame_provenance": record["provenance"]})
    atomic_json(ctx.out_dir / "assets.json", assets)
    # Snapshot records exact material actually admitted to this reconstruction.
    atomic_json(root / "datasets/_state" / request["scene"] / "manifest.json",
                {"run": request["run"], "assets": assets, "excluded": request["errors"]})
    if ctx.cfg.get("telemetry", {}).get("enabled"):
        telemetry.run_telemetry(ctx)


def features(root: Path, request: dict, ctx: Context) -> list[dict]:
    from .executor import extra_args_to_cli
    result = []
    profile = request["profile"]
    for asset in read_json(ctx.out_dir / "assets.json"):
        cache = root / "datasets/cache" / request["scene"] / "features" / asset["cache_key"]
        names = {i["name"] for i in asset["images"]}
        with lock(cache.parent.parent / "locks" / f"features-{asset['cache_key']}.lock"):
            record = read_json(cache / "record.json", {})
            if record:
                if record["sha256"] != asset["sha256"] or record["signature"] != request["feature_signature"]:
                    raise ConfigError("La caché no coincide con el contenido o parámetros")
                feature_database(cache / "database.db", names)
            else:
                temp = cache.with_name(cache.name + ".partial")
                if temp.exists():
                    shutil.rmtree(temp)
                temp.mkdir(parents=True)
                legacy = Path(asset["legacy"]) if asset.get("legacy") else None
                if legacy and legacy_allowed(profile) and asset["frame_provenance"] == "legacy-adoption":
                    feature_database(legacy / "database.db", names)
                    backup(legacy / "database.db", temp / "database.db")
                    provenance = {"origin": "conda-legacy", "colmap_version": "3.13.0",
                                  "parameters": "declared-from-original-job; validated against frames",
                                  "source": str(legacy), "source_sha256": file_hash(legacy / "database.db")}
                else:
                    fcfg = profile["features"]
                    cmd = ["colmap", "feature_extractor", "--database_path", temp / "database.db",
                           "--image_path", asset["frame_dir"], "--ImageReader.single_camera", "1",
                           "--ImageReader.camera_model", fcfg["camera_model"],
                           "--SiftExtraction.max_image_size", str(fcfg["max_image_size"]),
                           "--SiftExtraction.max_num_features", str(fcfg["max_num_features"]),
                           *general_flags("Extraction", fcfg["threads"], fcfg.get("gpu", False))]
                    if profile.get("masking", {}).get("enabled"):
                        mask_dir = temp / "masks"
                        for image in asset["images"]:
                            _link(ctx.masks_dir / (image["relative"] + ".png"), mask_dir / (image["name"] + ".png"))
                        cmd += ["--ImageReader.mask_path", mask_dir]
                    cmd += extra_args_to_cli(fcfg.get("extra_args"))
                    run_cmd(cmd, ctx.logs_dir / f"features-{asset['id']}.log")
                    provenance = {"origin": "worker", "colmap_version": profile["colmap_version"]}
                stats = feature_database(temp / "database.db", names)
                record = {"signature": request["feature_signature"], "sha256": asset["sha256"],
                          "stats": stats, "provenance": provenance}
                atomic_json(temp / "record.json", record)
                temp.rename(cache)
            result.append({"database": str(cache / "database.db"),
                           "names": {i["name"]: i["relative"] for i in asset["images"]},
                           "camera_group": asset["camera_group"]})
    return result


def _georef(ctx, request):
    if not ctx.cfg.get("georef", {}).get("enabled"):
        return
    cfg = ctx.cfg["georef"]
    if cfg.get("srt", {}).get("enabled"):
        if cfg.get("reference_file") or cfg.get("gpx", {}).get("file"):
            raise ConfigError("Elegir SRT, reference_file o GPX; no combinar referencias implícitamente")
        from .srt_positions import references
        cfg["reference_file"] = str(references(read_json(ctx.out_dir / "assets.json"),
                                              ctx.metrics_dir / "srt_positions.csv", cfg["srt"]))
    start = time.monotonic()
    georef.run_georef(ctx)
    atomic_json(ctx.markers_dir / "georef.done", {
        "snapshot": request["snapshot"], "elapsed_seconds": time.monotonic() - start,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "hostname": platform.node(), "slurm_job_id": os.environ.get("SLURM_JOB_ID")})


def prepare_database(root: Path, request: dict, ctx: Context):
    imports = features(root, request, ctx)
    db = ctx.colmap_dir / "database.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    parent_db = None
    if request["incremental"]:
        parent = root / request["parent"]["run"]
        parent_db = parent / "colmap/database.db"
    if request.get("reuse_sparse"):
        backup(parent_db, db)
        return
    consolidate(db, imports, parent_db, request["reuse_matches"])


def match_scene(root: Path, request: dict, ctx: Context):
    if request.get("reuse_sparse"):
        return
    images = [im for a in read_json(ctx.out_dir / "assets.json") for im in a["images"]]
    old_names = set()
    if request["incremental"] and request["reuse_matches"]:
        parent_db = root / request["parent"]["run"] / "colmap/database.db"
        with readonly(parent_db) as d:
            old_names = {r[0] for r in d.execute("SELECT name FROM images")}
    new_names = {i["relative"] for i in images} - old_names if old_names else None
    run_matching(ctx, images, request["profile"]["matching"], new_names)
    with readonly(ctx.colmap_dir / "database.db") as db:
        count = db.execute("SELECT COUNT(*) FROM two_view_geometries WHERE rows>0").fetchone()[0]
    if not count:
        raise ConfigError("No hay pares verificados geométricamente; no se inicia el mapper")


def map_scene(root: Path, request: dict, ctx: Context):
    parent_ctx = None
    if request["incremental"]:
        parent = root / request["parent"]["run"]
        parent_ctx = parent / "colmap/sparse" / (parent / "colmap/best_model.txt").read_text().strip()
    if request.get("reuse_sparse"):
        if (ctx.colmap_dir / "sparse").exists():
            shutil.rmtree(ctx.colmap_dir / "sparse")
        shutil.copytree(parent / "colmap/sparse", ctx.colmap_dir / "sparse")
        shutil.copy2(parent / "colmap/best_model.txt", ctx.colmap_dir / "best_model.txt")
        shutil.copy2(parent / "colmap/sparse.ply", ctx.colmap_dir / "sparse.ply")
        report = read_json(parent / "quality.json")
        for model in report["components"]:
            model["path"] = str(ctx.colmap_dir / "sparse" / Path(model["path"]).name)
        report["best"]["path"] = str(ctx.colmap_dir / "sparse" / Path(report["best"]["path"]).name)
        report["excluded"] = request["errors"]
        report["status"] = "partial" if request["errors"] or report["disconnected"] or not all(c["registered"] for c in report["clips"]) else "complete"
        atomic_json(ctx.out_dir / "quality.json", report)
        _georef(ctx, request)
        return
    images = [im for a in read_json(ctx.out_dir / "assets.json") for im in a["images"]]
    # A fresh mapping attempt never includes partial models from a failed attempt.
    if (ctx.colmap_dir / "sparse").exists():
        shutil.rmtree(ctx.colmap_dir / "sparse")
    report = run_mapping(ctx, request["profile"]["mapping"], parent_ctx)
    ratio = report["best"]["registered_images"] / len(images)
    report.update(registration_ratio=ratio, images_total=len(images), excluded=request["errors"])
    # Per-clip registration is checked using a text export, not file size.
    txt = ctx.colmap_dir / "model_txt"
    txt.mkdir(exist_ok=True)
    run_cmd(["colmap", "model_converter", "--input_path", report["best"]["path"],
             "--output_path", txt, "--output_type", "TXT"], ctx.logs_dir / "mapping.log")
    registered = set()
    lines = [line for line in (txt / "images.txt").read_text().splitlines() if not line.startswith("#")]
    for line in lines[::2]:
        parts = line.split()
        if len(parts) >= 10:
            registered.add(" ".join(parts[9:]))
    report["clips"] = [{"asset": a["relative"], "images": len(a["images"]),
                        "registered": sum(i["relative"] in registered for i in a["images"])}
                       for a in read_json(ctx.out_dir / "assets.json")]
    quality = request["profile"].get("quality", {})
    enough = (ratio >= quality.get("min_registration_ratio", 0.7)
              and report["best"].get("reprojection_error", float("inf")) <= quality.get("max_reprojection_error", 4)
              and report["best"].get("track_length", 0) >= quality.get("min_track_length", 2))
    report["status"] = "complete" if enough and not report["disconnected"] and not request["errors"] and all(c["registered"] for c in report["clips"]) else "partial"
    atomic_json(ctx.out_dir / "quality.json", report)
    if not enough:
        raise ConfigError("Sparse producido pero no pasa los umbrales; revisar quality.json")
    _georef(ctx, request)


def surface(root: Path, request: dict, ctx: Context):
    def step(name, fn, output):
        marker = ctx.markers_dir / f"{name}.done"
        if read_json(marker, {}).get("snapshot") == request["snapshot"] and output():
            return
        t = time.monotonic()
        fn()
        if not output():
            raise ConfigError(f"{name} terminó sin artefactos válidos")
        atomic_json(marker, {"snapshot": request["snapshot"], "elapsed_seconds": time.monotonic() - t,
                             "finished_at": datetime.now(timezone.utc).isoformat(),
                             "hostname": platform.node(), "slurm_job_id": os.environ.get("SLURM_JOB_ID")})

    step("undistort", lambda: sfm.run_undistort(ctx),
         lambda: (ctx.colmap_dir / "undistorted/images").is_dir()
         and any(p.suffix.lower() in frames.IMAGE_EXTS for p in (ctx.colmap_dir / "undistorted/images").rglob("*")))
    backend = ctx.cfg["dense"]["backend"]
    if backend == "openmvs":
        step("dense", lambda: mvs.run_densify(ctx), lambda: (ctx.mvs_dir / "scene_dense.mvs").is_file()
             and (ctx.mvs_dir / "scene_dense.ply").is_file())
        if request["target"] in ("mesh", "textured"):
            step("mesh", lambda: mvs.run_mesh(ctx), lambda: (ctx.mvs_dir / "scene_mesh.ply").is_file())
            step("clean", lambda: clean.run_clean(ctx), lambda: not clean.clean_enabled(ctx)
                 or (ctx.mvs_dir / "scene_mesh_clean.ply").is_file())
        if request["target"] == "textured":
            step("texture", lambda: mvs.run_texture(ctx), lambda: any(
                (ctx.mvs_dir / f"scene_texture.{ext}").is_file() for ext in ("obj", "ply", "glb")))
    elif backend == "colmap":
        step("dense", lambda: sfm.run_colmap_dense(ctx), lambda: sfm.colmap_fused_ply(ctx).is_file())
        if request["target"] in ("mesh", "textured"):
            step("mesh", lambda: sfm.run_colmap_mesher(ctx), lambda: any((ctx.colmap_dir / "dense").glob("meshed_*.ply")))
    else:
        raise ConfigError(f"Backend no soportado: {backend}")


def _run_record(ctx, request, status, **kwargs):
    return {"schema": 1, "scene": request["scene"], "experiment": request["experiment"],
            "status": status, "assets": request["assets"], "feature_signature": request["feature_signature"],
            "matching": request["profile"]["matching"], "target": request["target"],
            "mapping": request["profile"]["mapping"],
            "code_signature": request["code_signature"],
            "raw_dir": request["scene_config"]["raw_dir"],
            "runtime": {"colmap_version": request["profile"]["colmap_version"],
                        "container": os.environ.get("APPTAINER_CONTAINER"),
                        "hostname": platform.node(), "slurm_job_id": os.environ.get("SLURM_JOB_ID")},
            "snapshot": request["snapshot"], "parent": request["parent"], **kwargs}


def execute_phase(root: Path, request: dict, phase: str):
    if request["code_signature"] != code_signature(root):
        raise ConfigError("El código cambió después del envío. Crear una solicitud nueva; las features compatibles siguen en caché.")
    ctx = context(root, request)
    if phase == "surface":
        ctx.cfg["runtime"]["gpu"] = bool(request["profile"]["resources"]["surface"].get("gres"))
        ctx.cfg["runtime"]["num_threads"] = request["profile"]["resources"]["surface"]["cpus"]
    ctx.prepare()
    ctx.dump_resolved_config()
    state_dir = root / "datasets/_state" / request["scene"]
    with lock(state_dir / "worker.lock"):
        require_version(request["profile"]["colmap_version"]) if phase != "masks" else None
        marker = ctx.markers_dir / f"versioned-{phase}.done"
        # Resume only an exact immutable request. Sparse is revalidated on reuse.
        done = read_json(marker, {})
        if done.get("snapshot") == request["snapshot"]:
            if phase in ("features", "matching", "mapping"):
                feature_database(ctx.colmap_dir / "database.db")
                if phase == "mapping":
                    from .colmap.mapping import analyze
                    analyze(sfm.best_model_dir(ctx))
            elif phase == "prepare":
                for asset in read_json(ctx.out_dir / "assets.json"):
                    for image in asset["images"]:
                        image_dimensions(ctx.frames_dir / image["relative"])
            elif phase == "masks":
                masks.require_masks(ctx)
            elif phase == "surface":
                surface(root, request, ctx)
            return
        atomic_json(ctx.out_dir / "run.json", _run_record(ctx, request, "running", phase=phase))
        start = time.monotonic()
        try:
            if phase == "prepare":
                prepare(root, request, ctx)
            elif phase == "masks":
                masks.run_masks(ctx)
                masks.require_masks(ctx)
            elif phase == "features":
                prepare_database(root, request, ctx)
            elif phase == "matching":
                match_scene(root, request, ctx)
            elif phase == "mapping":
                map_scene(root, request, ctx)
            elif phase == "surface":
                surface(root, request, ctx)
            elif phase == "publish":
                publish(root, request, ctx)
            else:
                raise ConfigError(f"Fase desconocida: {phase}")
            atomic_json(marker, {"snapshot": request["snapshot"], "elapsed_seconds": time.monotonic() - start,
                                 "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
                                 "finished_at": datetime.now(timezone.utc).isoformat(), "hostname": platform.node()})
            # Keep the coarse stage aliases expected by legacy web clients.
            if phase in ("prepare", "masks", "mapping", "publish"):
                alias = {"prepare": "frames", "masks": "masks", "mapping": "sfm", "publish": "metrics"}[phase]
                atomic_json(ctx.markers_dir / f"{alias}.done",
                            read_json(marker))
        except Exception as exc:
            atomic_json(ctx.out_dir / "run.json", _run_record(ctx, request, "failed", phase=phase, error=str(exc)))
            raise


def publish(root, request, ctx):
    feature_database(ctx.colmap_dir / "database.db")
    quality = read_json(ctx.out_dir / "quality.json")
    if not quality:
        raise ConfigError("Falta validación de sparse")
    times = {p.stem: read_json(p).get("elapsed_seconds", 0) for p in ctx.markers_dir.glob("versioned-*.done")}
    run_metrics(ctx, times)
    metrics_file = ctx.metrics_dir / "metrics.json"
    metrics = read_json(metrics_file)
    metrics["versioned"] = {"request": request["id"], "parent": request["parent"],
                            "quality": quality, "feature_signature": request["feature_signature"]}
    atomic_json(metrics_file, metrics)
    record = _run_record(ctx, request, quality["status"], quality=quality)
    atomic_json(ctx.out_dir / "run.json", record)
    state = root / "datasets/_state" / request["scene"]
    previous = read_json(state / "last_result.json") or read_json(state / "latest.json")
    if previous and previous["run"] != request["run"] and previous != request.get("parent"):
        raise ConfigError("No se publica encima de una versión más reciente")
    pointer = {"run": request["run"], "snapshot": request["snapshot"], "status": quality["status"]}
    atomic_json(state / "last_result.json", pointer)
    if quality["status"] == "complete":
        atomic_json(state / "latest.json", pointer)
    # Partial results remain inspectable but cannot replace the complete latest.

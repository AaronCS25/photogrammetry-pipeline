"""Etapa 'frames': descubrimiento de fuentes e ingesta de media.

- Videos: extracción de frames con FFmpeg (fps, resize, recorte temporal).
- Fotos sueltas (jpg/png/...): se copian tal cual (preservando EXIF, que COLMAP
  usa como prior de focal y GPS) o se enlazan; con `resize` se reescalan vía
  ffmpeg (perdiendo EXIF, se advierte).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import Context
from .executor import CommandError, run_cmd

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


def _is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS


def _is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTS


def discover_sources(raw_dir: Path) -> dict[str, dict[str, list[Path]]]:
    """Mapa fuente -> {'videos': [...], 'photos': [...]}. Media en la raíz de la
    escena => fuente 'main'; cada subcarpeta con media es una fuente propia.

    Ojo: todas las fotos de una fuente deben tener las mismas dimensiones de
    píxel (por `single_camera_per_folder`); fotos verticales (píxeles rotados)
    van en su propia subcarpeta.
    """
    sources: dict[str, dict[str, list[Path]]] = {}

    def collect(paths: list[Path]) -> dict[str, list[Path]] | None:
        videos = sorted(p for p in paths if _is_video(p))
        photos = sorted(p for p in paths if _is_image(p))
        if videos or photos:
            return {"videos": videos, "photos": photos}
        return None

    root = collect(list(raw_dir.iterdir()))
    if root:
        sources["main"] = root
    for entry in sorted(raw_dir.iterdir()):
        if entry.is_dir():
            media = collect(list(entry.rglob("*")))
            if media:
                sources[entry.name] = media
    return sources


def sanitize_stem(stem: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stem)


def source_settings(cfg: dict, source: str) -> dict:
    """frames.* con overrides de sources.<fuente>.*"""
    base = dict(cfg.get("frames") or {})
    override = (cfg.get("sources") or {}).get(source) or {}
    base.update(override)
    return base


def _scale_filter(resize: dict | None) -> str | None:
    if not resize:
        return None
    if "long_edge" in resize:
        edge = int(resize["long_edge"])
        # Lado mayor a `edge`, el otro lado proporcional (par, requerido por libx/jpeg)
        return f"scale='if(gte(iw,ih),{edge},-2)':'if(gte(iw,ih),-2,{edge})'"
    if "width" in resize and "height" in resize:
        w, h = int(resize["width"]), int(resize["height"])
        # Encaja dentro de w x h manteniendo relación de aspecto
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease"
    raise CommandError(f"resize no reconocido: {resize} (usar long_edge o width+height)")


def build_ffmpeg_cmd(video: Path, out_pattern: Path, settings: dict) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    if settings.get("start"):
        cmd += ["-ss", str(settings["start"])]
    cmd += ["-i", str(video)]
    if settings.get("end"):
        cmd += ["-to", str(settings["end"])]

    filters = [f"fps={settings.get('fps', 2)}"]
    scale = _scale_filter(settings.get("resize"))
    if scale:
        filters.append(scale)
    cmd += ["-vf", ",".join(filters)]

    fmt = str(settings.get("format", "jpg")).lower()
    if fmt == "jpg":
        cmd += ["-qscale:v", str(settings.get("jpg_quality", 2))]
    elif fmt != "png":
        raise CommandError(f"frames.format no soportado: {fmt} (usar jpg o png)")

    cmd += [str(a) for a in (settings.get("extra_args") or [])]
    cmd += [str(out_pattern)]
    return cmd


def probe_video(video: Path) -> dict:
    """Metadatos del video vía ffprobe (mejor esfuerzo; {} si no está disponible)."""
    if shutil.which("ffprobe") is None:
        return {}
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,duration,nb_frames",
                "-of", "json", str(video),
            ],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        streams = json.loads(out).get("streams") or [{}]
        return streams[0]
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return {}


def _ingest_photos(ctx: Context, source: str, photos: list[Path],
                   source_dir: Path, settings: dict) -> int:
    """Ingesta de fotos sueltas: copia (default, preserva EXIF), symlink
    (ahorra disco) o reescalado vía ffmpeg si hay `resize` (pierde EXIF)."""
    resize = settings.get("resize")
    mode = str((settings.get("photos") or {}).get("mode", "copy")).lower()
    fmt = str(settings.get("format", "jpg")).lower()
    log = ctx.logs_dir / "frames.log"

    if resize:
        print(f"[frames] {source}: ADVERTENCIA: reescalar fotos con ffmpeg elimina el EXIF "
              "(prior de focal y GPS para COLMAP). Considerar resize: null en fuentes de fotos.")

    used_names: set[str] = set()
    count = 0
    for photo in photos:
        stem = sanitize_stem(photo.stem)
        suffix = f".{fmt}" if resize else photo.suffix.lower()
        name = f"{stem}{suffix}"
        serial = 1
        while name in used_names:
            name = f"{stem}_{serial}{suffix}"
            serial += 1
        used_names.add(name)
        dest = source_dir / name

        if resize:
            cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                   "-i", photo, "-vf", _scale_filter(resize)]
            if fmt == "jpg":
                cmd += ["-qscale:v", str(settings.get("jpg_quality", 2))]
            cmd += [dest]
            run_cmd(cmd, log, echo=False)
        elif mode == "symlink":
            dest.symlink_to(photo.resolve())
        elif mode == "copy":
            shutil.copy2(photo, dest)
        else:
            raise CommandError(f"frames.photos.mode no soportado: {mode} (usar copy o symlink)")
        count += 1
    return count


def run_frames(ctx: Context) -> None:
    sources = discover_sources(ctx.raw_dir)
    if not sources:
        raise CommandError(
            f"No se encontró media en {ctx.raw_dir} (videos: "
            f"{', '.join(sorted(VIDEO_EXTS))}; fotos: {', '.join(sorted(IMAGE_EXTS))})"
        )

    media_info: dict[str, dict] = {}
    for source, media in sources.items():
        settings = source_settings(ctx.cfg, source)
        fmt = str(settings.get("format", "jpg")).lower()
        source_dir = ctx.frames_dir / source
        # Ingesta limpia: borrar frames previos de esta fuente
        if source_dir.exists():
            shutil.rmtree(source_dir)
        source_dir.mkdir(parents=True, exist_ok=True)

        for video in media["videos"]:
            stem = sanitize_stem(video.stem)
            pattern = source_dir / f"{stem}_%06d.{fmt}"
            log = ctx.logs_dir / "frames.log"
            run_cmd(build_ffmpeg_cmd(video, pattern, settings), log)
            count = len(list(source_dir.glob(f"{stem}_*.{fmt}")))
            if count == 0:
                raise CommandError(f"ffmpeg no produjo frames para {video}")
            media_info[f"{source}/{video.name}"] = {
                "frames_extracted": count,
                "settings": {k: settings.get(k) for k in ("fps", "resize", "format", "start", "end")},
                "probe": probe_video(video),
            }
            print(f"[frames] {source}/{video.name}: {count} frames")

        if media["photos"]:
            count = _ingest_photos(ctx, source, media["photos"], source_dir, settings)
            media_info[f"{source}/(fotos)"] = {
                "photos_ingested": count,
                "settings": {"resize": settings.get("resize"),
                             "mode": (settings.get("photos") or {}).get("mode", "copy")},
            }
            print(f"[frames] {source}: {count} fotos ingestadas")

    with open(ctx.metrics_dir / "videos_info.json", "w", encoding="utf-8") as fh:
        json.dump(media_info, fh, indent=2, ensure_ascii=False)

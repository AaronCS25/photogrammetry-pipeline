"""Etapa 'frames': descubrimiento de fuentes y extracción de frames con FFmpeg."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import Context
from .executor import CommandError, run_cmd

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".webm"}


def _is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS


def discover_sources(raw_dir: Path) -> dict[str, list[Path]]:
    """Mapa fuente -> videos. Videos en la raíz de la escena => fuente 'main';
    cada subcarpeta con videos es una fuente con su nombre."""
    sources: dict[str, list[Path]] = {}
    root_videos = sorted(p for p in raw_dir.iterdir() if _is_video(p))
    if root_videos:
        sources["main"] = root_videos
    for entry in sorted(raw_dir.iterdir()):
        if entry.is_dir():
            videos = sorted(p for p in entry.rglob("*") if _is_video(p))
            if videos:
                sources[entry.name] = videos
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


def run_frames(ctx: Context) -> None:
    sources = discover_sources(ctx.raw_dir)
    if not sources:
        raise CommandError(
            f"No se encontraron videos en {ctx.raw_dir} "
            f"(extensiones soportadas: {', '.join(sorted(VIDEO_EXTS))})"
        )

    videos_info: dict[str, dict] = {}
    for source, videos in sources.items():
        settings = source_settings(ctx.cfg, source)
        fmt = str(settings.get("format", "jpg")).lower()
        source_dir = ctx.frames_dir / source
        # Extracción limpia: borrar frames previos de esta fuente
        if source_dir.exists():
            shutil.rmtree(source_dir)
        source_dir.mkdir(parents=True, exist_ok=True)

        for video in videos:
            stem = sanitize_stem(video.stem)
            pattern = source_dir / f"{stem}_%06d.{fmt}"
            log = ctx.logs_dir / "frames.log"
            run_cmd(build_ffmpeg_cmd(video, pattern, settings), log)
            count = len(list(source_dir.glob(f"{stem}_*.{fmt}")))
            if count == 0:
                raise CommandError(f"ffmpeg no produjo frames para {video}")
            videos_info[f"{source}/{video.name}"] = {
                "frames_extracted": count,
                "settings": {k: settings.get(k) for k in ("fps", "resize", "format", "start", "end")},
                "probe": probe_video(video),
            }
            print(f"[frames] {source}/{video.name}: {count} frames")

    with open(ctx.metrics_dir / "videos_info.json", "w", encoding="utf-8") as fh:
        json.dump(videos_info, fh, indent=2, ensure_ascii=False)

"""Cheap read-only planning inventory; full hashes/decoding run in workers."""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .frames import IMAGE_EXTS, VIDEO_EXTS
from .state import digest


def scan(scene: dict) -> dict:
    root = Path(scene["raw_dir"])
    items, errors = [], []
    if not root.is_dir():
        return {"items": [], "errors": [{"path": str(root), "reason": "Carpeta inexistente"}]}
    excludes = scene.get("exclude", ["*_map.png", "mapa*", "*.LRF", "*.part"])
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or any(x.startswith(("_", ".")) for x in rel.parts):
            continue
        if any(fnmatch.fnmatch(p.name, pattern) or fnmatch.fnmatch(rel.as_posix(), pattern)
               for pattern in excludes):
            continue
        ext = p.suffix.lower()
        if ext not in IMAGE_EXTS | VIDEO_EXTS:
            continue
        with p.open("rb") as f:
            header = f.read(512).lstrip().lower()
        if not header or header.startswith((b"<!doctype html", b"<html")):
            errors.append({"path": rel.as_posix(), "reason": "Vacío o HTML en lugar de media"})
            continue
        st = p.stat()
        asset_id = digest(rel.as_posix())[:20]
        source = rel.parts[0] if len(rel.parts) > 1 else "drone"
        groups = scene.get("camera_groups", {})
        group = groups.get(source, f"asset_{asset_id}")
        source = re.sub(r"[^A-Za-z0-9_.-]", "_", source)
        items.append({"id": asset_id, "relative": rel.as_posix(), "path": str(p),
                      "source": source, "camera_group": group,
                      "kind": "video" if ext in VIDEO_EXTS else "photo",
                      "sidecars": [{"path": str(s), "size": s.stat().st_size, "mtime_ns": s.stat().st_mtime_ns}
                                   for suffix in (".SRT", ".srt") if (s := p.with_suffix(suffix)).is_file()],
                      "stamp": {"size": st.st_size, "mtime_ns": st.st_mtime_ns}})
    if not items and not errors:
        errors.append({"path": str(root), "reason": "No hay fotos ni videos"})
    return {"items": items, "errors": errors}

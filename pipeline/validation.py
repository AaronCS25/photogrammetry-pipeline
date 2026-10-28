"""Validate actual contents, not exit codes or file existence alone."""
from __future__ import annotations

import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path

from .config import ConfigError


@contextmanager
def readonly(path: Path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield db
    finally:
        db.close()


@contextmanager
def writable(path: Path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def feature_database(path: Path, names: set[str] | None = None) -> dict:
    with readonly(path) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ConfigError(f"SQLite corrupto: {path}")
        rows = db.execute("""SELECT i.name,k.rows,k.cols,length(k.data),d.rows,d.cols,length(d.data)
            FROM images i LEFT JOIN keypoints k USING(image_id)
            LEFT JOIN descriptors d USING(image_id)""").fetchall()
        if not rows or (names is not None and {r[0] for r in rows} != names):
            raise ConfigError(f"Imágenes incompletas en {path}")
        for name, n, cols, length, dn, dc, dl in rows:
            if (n is None or n <= 0 or cols not in (2, 4, 6) or length != n * cols * 4
                    or dn != n or dc != 128 or dl != dn * dc):
                raise ConfigError(f"Features SIFT inválidas: {path}: {name}")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ConfigError(f"Referencias inconsistentes: {path}")
        return {"images": len(rows), "features": sum(r[1] for r in rows)}


def probe_video(path: Path) -> dict:
    import json
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=width,height,duration:format=duration",
                             "-of", "json", str(path)], capture_output=True, text=True)
    if result.returncode:
        raise ConfigError(f"Video no decodificable: {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise ConfigError(f"Sin stream de video: {path}")
    return data


def image_dimensions(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        size = im.size
        im.verify()
    return size

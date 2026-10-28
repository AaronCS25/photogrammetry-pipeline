"""Optional DJI SRT -> per-frame GPS references for the existing georef stage.

Only absolute altitude is accepted. Relative flight height is not geodetic
altitude. Sampling timestamps are the declared FFmpeg output timeline, not
precision surveyed camera positions; an explicit shift can correct offsets.
"""
from __future__ import annotations

import bisect
import csv
import math
from pathlib import Path

from .config import ConfigError
from .state import atomic_json
from .telemetry import parse_srt


def seconds(value: str) -> float:
    parts = str(value).replace(",", ".").split(":")
    return sum(float(v) * 60 ** i for i, v in enumerate(reversed(parts)))


def position(rows: list[dict], t: float, max_gap: float) -> tuple | None:
    times = [r["t"] for r in rows]
    if not times:
        return None
    i = bisect.bisect_left(times, t)
    if i < len(rows) and abs(times[i] - t) < 1e-6:
        return tuple(rows[i][k] for k in ("latitude", "longitude", "abs_alt"))
    if i == 0 or i == len(rows):
        return None  # No extrapolation beyond the captured track.
    a, b = rows[i - 1], rows[i]
    if t - a["t"] > max_gap or b["t"] - t > max_gap or b["t"] == a["t"]:
        return None
    alpha = (t - a["t"]) / (b["t"] - a["t"])
    return tuple(a[k] + alpha * (b[k] - a[k]) for k in ("latitude", "longitude", "abs_alt"))


def references(assets: list[dict], output: Path, cfg: dict) -> Path:
    refs, skipped = [], 0
    for asset in assets:
        if asset["kind"] != "video":
            continue
        video = Path(asset["path"])
        srt = next((video.with_suffix(s) for s in (".SRT", ".srt") if video.with_suffix(s).is_file()), None)
        rows = []
        for row in parse_srt(srt) if srt else []:
            if not all(k in row for k in ("timestamp", "latitude", "longitude", "abs_alt")):
                continue
            lat, lon, alt = row["latitude"], row["longitude"], row["abs_alt"]
            if not all(math.isfinite(v) for v in (lat, lon, alt)) or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
                continue
            rows.append({**row, "t": seconds(row["timestamp"])})
        rows.sort(key=lambda r: r["t"])
        for image in asset["images"]:
            point = position(rows, image["time_s"] + cfg.get("time_shift_s", 0), cfg.get("max_gap_s", 1))
            if point:
                refs.append((image["relative"], *point))
            else:
                skipped += 1
    if len(refs) < cfg.get("min_images", 3):
        raise ConfigError("SRT: insuficientes posiciones con altitud absoluta; no se sustituye por altura relativa")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("name", "lat", "lon", "alt"))
        writer.writerows(refs)
    atomic_json(output.with_suffix(".json"), {"positions": len(refs), "skipped": skipped,
                "altitude": "DJI abs_alt; verificar datum para alineación métrica",
                "timeline": "sampled frames", "settings": cfg})
    return output

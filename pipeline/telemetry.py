"""Etapa 'telemetry': parseo de subtítulos .srt de DJI a CSV (lat/lon/alt por bloque).

Soporta los dos formatos habituales de DJI:
  - Moderno:  [latitude: -12.0464] [longitude: -77.0428] [rel_alt: 25.3 abs_alt: 130.1]
  - Antiguo:  GPS(-77.0428,-12.0464,0.0) ... H 25.3m
Si un .srt no coincide con ningún patrón se copia tal cual y se deja constancia.
"""

from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path

from .config import Context
from .frames import discover_sources

_PATTERNS = {
    "latitude": re.compile(r"\[latitude\s*:\s*(-?\d+(?:\.\d+)?)\]", re.IGNORECASE),
    "longitude": re.compile(r"\[longitude\s*:\s*(-?\d+(?:\.\d+)?)\]", re.IGNORECASE),
    "rel_alt": re.compile(r"\[?rel_alt\s*:\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    "abs_alt": re.compile(r"abs_alt\s*:\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
}
_GPS_LEGACY = re.compile(r"GPS\s*\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")
_TIMESTAMP = re.compile(r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->")


def parse_srt(srt_path: Path) -> list[dict]:
    text = srt_path.read_text(encoding="utf-8", errors="replace")
    rows: list[dict] = []
    # Bloques separados por líneas en blanco
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        row: dict = {}
        ts = _TIMESTAMP.search(block)
        if ts:
            row["timestamp"] = ts.group(1).replace(",", ".")
        for key, pattern in _PATTERNS.items():
            m = pattern.search(block)
            if m:
                row[key] = float(m.group(1))
        if "latitude" not in row:
            legacy = _GPS_LEGACY.search(block)
            if legacy:
                row["longitude"] = float(legacy.group(1))
                row["latitude"] = float(legacy.group(2))
        if "latitude" in row or "longitude" in row:
            rows.append(row)
    return rows


def run_telemetry(ctx: Context) -> None:
    found = 0
    for source, media in discover_sources(ctx.raw_dir).items():
        for video in media["videos"]:
            candidates = [video.with_suffix(ext) for ext in (".srt", ".SRT")]
            srt = next((c for c in candidates if c.is_file()), None)
            if srt is None:
                continue
            found += 1
            out_dir = ctx.telemetry_dir / source
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(srt, out_dir / srt.name)

            rows = parse_srt(srt)
            if not rows:
                print(f"[telemetry] {srt.name}: formato no reconocido, solo se copió el original")
                continue
            fields = ["index", "timestamp", "latitude", "longitude", "rel_alt", "abs_alt"]
            csv_path = out_dir / (video.stem + "_telemetry.csv")
            with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                for i, row in enumerate(rows):
                    writer.writerow({"index": i, **{k: row.get(k, "") for k in fields[1:]}})
            print(f"[telemetry] {srt.name}: {len(rows)} registros -> {csv_path.name}")
    if found == 0:
        print("[telemetry] no se encontraron archivos .srt (etapa sin efecto)")

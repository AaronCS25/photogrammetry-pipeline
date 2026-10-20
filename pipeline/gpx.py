"""Posiciones de cámara desde un track GPX (app de registro GPS) emparejado por
HORA DE CAPTURA con el EXIF de cada foto.

Por qué: el GPS que escribe la app de cámara del teléfono es inservible
(posiciones cacheadas, saltos de cientos de metros). Una app de registro
(GPS Logger, OsmAnd...) graba un punto por segundo con el receptor activo. El
EXIF de la foto trae `DateTimeOriginal` (hora local del teléfono, sin zona) y,
en teléfonos recientes, `OffsetTimeOriginal` ("-05:00"); el GPX trae `<time>`
en UTC. Con ambos se interpola la posición de cada foto sobre el track.

Salida: CSV `file,lat,lon,alt` con el mismo formato que `georef.reference_file`,
de modo que el resto de la etapa georef (model_aligner ENU) no cambia.

Sin dependencias externas: xml.etree para el GPX, PIL para el EXIF.
"""

from __future__ import annotations

import bisect
import csv
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .executor import CommandError

_EXIF_DATETIME_ORIGINAL = 0x9003
_EXIF_SUBSEC_ORIGINAL = 0x9291
_EXIF_OFFSET_ORIGINAL = 0x9011
_EXIF_IFD = 0x8769
_NAME_TIME = re.compile(r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})")


def parse_gpx(path: Path) -> list[tuple[float, float, float, float]]:
    """Puntos (t_utc_s, lat, lon, ele) de todos los <trkpt> (y <wpt>) con
    <time>, ordenados por tiempo. Ignora el namespace del GPX."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise CommandError(f"GPX inválido {path}: {exc}")
    points: list[tuple[float, float, float, float]] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("trkpt", "wpt"):
            continue
        t = ele = None
        for child in el:
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag == "time" and child.text:
                t = _parse_iso_utc(child.text.strip())
            elif ctag == "ele" and child.text:
                try:
                    ele = float(child.text)
                except ValueError:
                    ele = None
        if t is None:
            continue
        try:
            lat, lon = float(el.get("lat")), float(el.get("lon"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)) or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
            continue
        points.append((t, lat, lon, ele if ele is not None and math.isfinite(ele) else 0.0))
    points.sort()
    if not points:
        raise CommandError(f"GPX sin puntos con <time>: {path}")
    return points


def _parse_iso_utc(text: str) -> float | None:
    """'2026-10-21T15:07:40Z' | '...+00:00' | '...-05:00' | con fracción -> epoch UTC."""
    s = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def exif_capture_time(image: Path, default_utc_offset_h: float) -> float | None:
    """Epoch UTC de DateTimeOriginal (+ SubSecTimeOriginal), usando
    OffsetTimeOriginal si existe y si no `default_utc_offset_h`. Si no hay EXIF,
    intenta el patrón IMG_AAAAMMDD_HHMMSS del nombre. None si no hay hora."""
    text = subsec = offset = None
    try:
        from PIL import Image
        with Image.open(image) as img:
            exif = img.getexif()
            ifd = exif.get_ifd(_EXIF_IFD) if exif else {}
            text = ifd.get(_EXIF_DATETIME_ORIGINAL) or exif.get(0x0132)
            subsec = ifd.get(_EXIF_SUBSEC_ORIGINAL)
            offset = ifd.get(_EXIF_OFFSET_ORIGINAL)
    except Exception:   # sin PIL, archivo no imagen, EXIF corrupto: se intenta el nombre
        text = None
    if text:
        try:
            dt = datetime.strptime(str(text).strip()[:19], "%Y:%m:%d %H:%M:%S")
        except ValueError:
            dt = None
    else:
        dt = None
    if dt is None:
        m = _NAME_TIME.search(image.stem)
        if not m:
            return None
        dt = datetime(*(int(g) for g in m.groups()))
        subsec = None
    tz = _parse_offset(offset) if offset else timedelta(hours=float(default_utc_offset_h))
    dt = dt.replace(tzinfo=timezone(tz))
    frac = 0.0
    if subsec:
        digits = re.sub(r"\D", "", str(subsec))
        if digits:
            frac = float("0." + digits)
    return dt.timestamp() + frac


def _parse_offset(text) -> timedelta:
    m = re.match(r"\s*([+-])(\d{1,2}):?(\d{2})", str(text))
    if not m:
        return timedelta(0)
    sign = 1 if m.group(1) == "+" else -1
    return sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))


def interpolate(track: list[tuple[float, float, float, float]], t: float,
                max_gap_s: float) -> tuple[float, float, float] | None:
    """Posición en el instante t por interpolación lineal entre los dos puntos
    que lo rodean. None si el punto más cercano está a más de max_gap_s."""
    times = [p[0] for p in track]
    i = bisect.bisect_left(times, t)
    if i == 0:
        p = track[0]
        return (p[1], p[2], p[3]) if abs(p[0] - t) <= max_gap_s else None
    if i >= len(track):
        p = track[-1]
        return (p[1], p[2], p[3]) if abs(t - p[0]) <= max_gap_s else None
    a, b = track[i - 1], track[i]
    if min(t - a[0], b[0] - t) > max_gap_s:
        return None
    span = b[0] - a[0]
    w = 0.0 if span <= 0 else (t - a[0]) / span
    return (a[1] + w * (b[1] - a[1]), a[2] + w * (b[2] - a[2]), a[3] + w * (b[3] - a[3]))


def gpx_positions(gpx_path: Path, images: dict[str, Path], out_csv: Path, *,
                  utc_offset_h: float = -5.0, time_shift_s: float = 0.0,
                  max_gap_s: float = 5.0) -> dict:
    """Escribe out_csv (file,lat,lon,alt) con la posición de cada imagen
    {nombre_colmap: ruta_del_archivo} y devuelve un resumen.

    time_shift_s se SUMA a la hora de la foto (reloj del teléfono adelantado
    3 s respecto al GPS -> time_shift_s: -3).
    """
    track = parse_gpx(gpx_path)
    t0, t1 = track[0][0], track[-1][0]
    rows: list[tuple[str, float, float, float]] = []
    no_time: list[str] = []
    outside: list[str] = []
    for name, path in images.items():
        t = exif_capture_time(path, utc_offset_h)
        if t is None:
            no_time.append(name)
            continue
        pos = interpolate(track, t + time_shift_s, max_gap_s)
        if pos is None:
            outside.append(name)
            continue
        rows.append((name, pos[0], pos[1], pos[2]))
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "lat", "lon", "alt"])
        for name, lat, lon, alt in rows:
            w.writerow([name, f"{lat:.8f}", f"{lon:.8f}", f"{alt:.2f}"])
    # Diagnóstico de sincronía: cuánto quedan las fotos dentro del track
    times = [exif_capture_time(p, utc_offset_h) for p in images.values()]
    times = [t + time_shift_s for t in times if t is not None]
    summary = {
        "gpx": gpx_path.name, "track_points": len(track),
        "track_utc": [_iso(t0), _iso(t1)],
        "photos_utc": [_iso(min(times)), _iso(max(times))] if times else None,
        "utc_offset_h": utc_offset_h, "time_shift_s": time_shift_s, "max_gap_s": max_gap_s,
        "positioned": len(rows), "without_time": len(no_time), "outside_track": len(outside),
    }
    if times and (max(times) < t0 or min(times) > t1):
        summary["warning"] = ("las fotos caen FUERA del intervalo del track: revisar utc_offset_h "
                              "(zona horaria del teléfono) o time_shift_s")
    return summary


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

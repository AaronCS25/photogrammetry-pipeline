"""Subcomando 'report': consolida todos los metrics.json en un CSV comparativo."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .config import REPO_ROOT


def _flatten(data: dict, prefix: str = "") -> dict:
    flat: dict = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        elif isinstance(value, (list, tuple)):
            flat[name] = json.dumps(value, ensure_ascii=False)
        else:
            flat[name] = value
    return flat


def run_report(output_root: str | None, out_csv: str | None) -> Path:
    root = Path(output_root) if output_root else REPO_ROOT / "outputs"
    if not root.is_absolute():
        root = REPO_ROOT / root
    rows = []
    for metrics_file in sorted(root.glob("*/*/metrics/metrics.json")):
        try:
            data = json.loads(metrics_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[report] omitiendo {metrics_file}: {exc}")
            continue
        # Los tamaños/rutas de artefactos hacen ruido en el CSV comparativo
        data.pop("artifacts", None)
        rows.append(_flatten(data))

    if not rows:
        raise SystemExit(f"[report] no se encontraron metrics.json bajo {root}")

    columns: list[str] = []
    for row in rows:
        for col in row:
            if col not in columns:
                columns.append(col)

    out_path = Path(out_csv) if out_csv else root / "experiments_summary.csv"
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[report] {len(rows)} experimentos -> {out_path}")
    return out_path

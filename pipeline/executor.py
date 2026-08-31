"""Ejecución de comandos externos con log por etapa, y gestión de etapas reanudables."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class CommandError(RuntimeError):
    pass


def format_cmd(cmd: list) -> str:
    return " ".join(str(c) for c in cmd)


def run_cmd(cmd: list, log_path: Path, cwd: Path | None = None, echo: bool = True) -> float:
    """Ejecuta `cmd`, tee de stdout+stderr al log (y a stdout para el .out de SLURM).

    Devuelve el tiempo transcurrido en segundos; lanza CommandError si falla.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        header = f"\n[{datetime.now().isoformat(timespec='seconds')}] $ {format_cmd(cmd)}\n"
        log.write(header)
        log.flush()
        if echo:
            sys.stdout.write(header)
            sys.stdout.flush()
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(cwd) if cwd else None,
            text=True,
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            if echo:
                sys.stdout.write(line)
        returncode = proc.wait()
        log.flush()
    elapsed = time.time() - start
    if returncode != 0:
        raise CommandError(
            f"Comando falló (exit {returncode}): {format_cmd(cmd)}\nVer log: {log_path}"
        )
    return elapsed


def extra_args_to_cli(extra: dict | None) -> list[str]:
    """Convierte {opcion: valor} en ['--opcion', 'valor'] (bools -> 1/0)."""
    args: list[str] = []
    for key, value in (extra or {}).items():
        if isinstance(value, bool):
            value = int(value)
        args += [f"--{key}", str(value)]
    return args


class StageRunner:
    """Ejecuta etapas con marcadores .done (reanudación) y registro de tiempos."""

    def __init__(self, markers_dir: Path, force: bool = False):
        self.markers_dir = markers_dir
        self.force = force
        self.timings: dict[str, float] = {}
        self.executed: list[str] = []
        self.skipped: list[str] = []

    def _marker(self, name: str) -> Path:
        return self.markers_dir / f"{name}.done"

    def is_done(self, name: str) -> bool:
        return self._marker(name).exists()

    def clear(self, name: str) -> None:
        self._marker(name).unlink(missing_ok=True)

    def run(self, name: str, fn) -> None:
        if self.is_done(name) and not self.force:
            print(f"[pipeline] etapa '{name}': ya completada, se omite (usar --force para repetir)")
            self.skipped.append(name)
            return
        self.clear(name)
        print(f"[pipeline] etapa '{name}': iniciando")
        start = time.time()
        fn()
        elapsed = time.time() - start
        self.timings[name] = round(elapsed, 2)
        self.executed.append(name)
        self.markers_dir.mkdir(parents=True, exist_ok=True)
        self._marker(name).write_text(
            json.dumps(
                {
                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "elapsed_seconds": self.timings[name],
                }
            ),
            encoding="utf-8",
        )
        print(f"[pipeline] etapa '{name}': completada en {elapsed:,.1f} s")

    def collected_timings(self) -> dict[str, float]:
        """Tiempos de este run + los de runs anteriores (leídos de los marcadores)."""
        timings = {}
        for marker in sorted(self.markers_dir.glob("*.done")):
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
                timings[marker.stem] = data.get("elapsed_seconds")
            except (json.JSONDecodeError, OSError):
                timings[marker.stem] = None
        timings.update(self.timings)
        return timings

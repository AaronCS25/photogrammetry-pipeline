"""Ejecución de comandos externos con log por etapa, y gestión de etapas reanudables."""

from __future__ import annotations

import json
import os
import platform
import queue
import signal
import shutil
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path


class CommandError(RuntimeError):
    pass


def check_free_space() -> None:
    root = os.environ.get("PG_DATA_ROOT")
    if not root:
        return
    free = shutil.disk_usage(root).free / 1024**3
    minimum = float(os.environ.get("PG_MIN_FREE_GIB", "20"))
    if free < minimum:
        raise CommandError(f"Espacio insuficiente en {root}: {free:.1f} GiB libres, mínimo {minimum:g}")


def _terminate(proc) -> None:
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


def format_cmd(cmd: list) -> str:
    return " ".join(str(c) for c in cmd)


def run_cmd(cmd: list, log_path: Path, cwd: Path | None = None, echo: bool = True) -> float:
    """Ejecuta `cmd`, tee de stdout+stderr al log (y a stdout para el .out de SLURM).

    Devuelve el tiempo transcurrido en segundos; lanza CommandError si falla.
    """
    check_free_space()
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
            start_new_session=True,
        )
        assert proc.stdout is not None
        lines = queue.Queue(maxsize=512)
        def pump():
            try:
                for line in proc.stdout:
                    lines.put(line)
            finally:
                proc.stdout.close()
                lines.put(None)
        threading.Thread(target=pump, daemon=True).start()
        next_check = time.monotonic()
        try:
            while True:
                if time.monotonic() >= next_check:
                    check_free_space()
                    next_check = time.monotonic() + 2
                try:
                    line = lines.get(timeout=1)
                except queue.Empty:
                    continue
                if line is None:
                    break
                log.write(line)
                if echo:
                    sys.stdout.write(line)
            returncode = proc.wait()
        except BaseException:
            _terminate(proc)
            raise
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

    def __init__(self, markers_dir: Path, force: bool = False, validator=None):
        self.markers_dir = markers_dir
        self.force = force
        self.validator = validator
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
            if self.validator:
                self.validator(name)
            print(f"[pipeline] etapa '{name}': ya completada, se omite (usar --force para repetir)")
            self.skipped.append(name)
            return
        self.clear(name)
        print(f"[pipeline] etapa '{name}': iniciando")
        start = time.time()
        fn()
        if self.validator:
            self.validator(name)
        elapsed = time.time() - start
        self.timings[name] = round(elapsed, 2)
        self.executed.append(name)
        self.markers_dir.mkdir(parents=True, exist_ok=True)
        marker = self._marker(name)
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "elapsed_seconds": self.timings[name],
                    "hostname": platform.node(),
                    "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
                    "image_id": os.environ.get("PG_IMAGE_ID"),
                    "role": os.environ.get("PG_RUN_ROLE", "reconstruction"),
                }
            ),
            encoding="utf-8",
        )
        temporary.replace(marker)
        print(f"[pipeline] etapa '{name}': completada en {elapsed:,.1f} s")

    def collected_stage_info(self) -> dict[str, dict]:
        """Contenido completo de los marcadores (tiempos, nodo, job) por etapa."""
        info: dict[str, dict] = {}
        for marker in sorted(self.markers_dir.glob("*.done")):
            try:
                info[marker.stem] = json.loads(marker.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return info

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

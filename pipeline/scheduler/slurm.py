"""SLURM dependency chain, durable receipts, and explicit uncertain submissions."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..config import ConfigError
from ..scenes import resolve
from ..state import atomic_json, read_json

ACTIVE = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED", "REQUEUED", "RESIZING"}


def reconcile(path: Path) -> dict:
    """Recover a lost sbatch acknowledgement by exact request/phase comment."""
    receipt_path = path.with_suffix(".receipt.json")
    receipt = read_json(receipt_path)
    request = read_json(path)
    if not receipt or receipt.get("status") != "uncertain":
        return receipt or {}
    phase = receipt["attempting"]["phase"]
    expected = f"scene:{request['scene']}:request:{request['id']}:phase:{phase}"
    commands = [["squeue", "--me", "-h", "-o", "%i|%k"],
                ["sacct", "-X", "-n", "-P", "--user", os.environ.get("USER", ""),
                 "-S", "now-7days", "--format=JobID,Comment"]]
    found = set()
    for args in commands:
        p = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if p.returncode:
            continue
        for line in p.stdout.splitlines():
            fields = line.strip().split("|")
            if len(fields) >= 2 and fields[0].isdigit() and fields[1] == expected:
                found.add(fields[0])
    if len(found) != 1:
        raise ConfigError(f"No hay un único job confirmado ({sorted(found)}). El recibo sigue incierto; no se reenvía.")
    job = found.pop()
    if not any(j["job"] == job for j in receipt["jobs"]):
        receipt["jobs"].append({"job": job, "phase": phase, "name": "photogram"})
    receipt.update(status="submitting", attempting=None)
    atomic_json(receipt_path, receipt)
    return receipt


def job_state(job: str) -> str:
    p = subprocess.run(["squeue", "-h", "-j", str(job), "-o", "%T"], capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip().splitlines()[0]
    p = subprocess.run(["sacct", "-X", "-n", "-P", "-j", str(job), "--format=State"], capture_output=True, text=True)
    return p.stdout.strip().split("|")[0].split()[0] if p.returncode == 0 and p.stdout.strip() else "UNKNOWN"


def phases(request: dict) -> list[str]:
    out = ["prepare"]
    if request["profile"].get("masking", {}).get("enabled"):
        out.append("masks")
    out += ["features", "matching", "mapping"]
    if request["target"] != "sparse":
        out.append("surface")
    return out + ["publish"]


def submit(root: Path, path: Path, request: dict, resume: bool = False) -> dict:
    runtime = request["profile"]["runtime"]
    resources = request["profile"]["resources"]
    receipt_path = path.with_suffix(".receipt.json")
    receipt = read_json(receipt_path, {"request": request["id"], "jobs": [], "status": "new"})
    if receipt["status"] == "submitted" and not resume:
        return receipt
    if receipt["status"] == "uncertain":
        raise ConfigError("Envío incierto: reconciliar con squeue/sacct antes de reintentar; no se duplica automáticamente")
    if resume:
        states = [job_state(j["job"]) for j in receipt["jobs"]]
        if any(s in ACTIVE or s == "UNKNOWN" for s in states):
            raise ConfigError("Hay jobs activos o de estado desconocido; no se puede reanudar aún")
        receipt = {"request": request["id"], "jobs": [], "previous": receipt, "status": "new"}
    if runtime.get("engine") != "apptainer":
        raise ConfigError("El envío Khipu requiere runtime.engine: apptainer")
    for phase in phases(request):
        image = runtime.get("segmentation_image") if phase == "masks" else runtime.get("image")
        if not image or not resolve(root, image).is_file():
            raise ConfigError(f"Falta imagen para {phase}: {image}. Construirla y verificarla antes de enviar.")
        if phase not in resources:
            raise ConfigError(f"Falta perfil de recursos para {phase}")
        r = resources[phase]
        limits = request["profile"].get("limits", {"cpus": 32, "mem_gb": 98})
        if r["cpus"] < 1 or r["mem_gb"] < 1 or r["cpus"] > limits["cpus"] or r["mem_gb"] > limits["mem_gb"]:
            raise ConfigError(f"Recursos de {phase} superan los límites configurados")
        if phase in ("features", "matching", "mapping"):
            threads = request["profile"][phase]["threads"]
            if threads > r["cpus"]:
                raise ConfigError(f"Hilos configurados exceden cpus del job {phase}")
            if request["profile"][phase].get("gpu", False) and not r.get("gres"):
                raise ConfigError(f"{phase} GPU requiere gres")
    logs = root / "slurm-logs"
    logs.mkdir(exist_ok=True)
    dependency = receipt["jobs"][-1]["job"] if receipt["jobs"] else request.get("after_job")
    done = {j["phase"] for j in receipt["jobs"]}
    for phase in phases(request):
        if phase in done:
            continue
        r = resources[phase]
        # Each phase stays below the configured account budget. Global account/QOS
        # concurrency remains enforced by Slurm, not guessed from stale sinfo.
        limits = request["profile"].get("limits", {"cpus": 32, "mem_gb": 98})
        if r["cpus"] > limits["cpus"] or r["mem_gb"] > limits["mem_gb"]:
            raise ConfigError(f"Recursos de {phase} superan los límites configurados")
        name = "photogram"  # Preserve the existing studio's job/log discovery.
        args = ["sbatch", "--parsable", f"--job-name={name}", f"--chdir={root}",
                f"--comment=scene:{request['scene']}:request:{request['id']}:phase:{phase}",
                f"--output={logs}/%x-%j.out", f"--error={logs}/%x-%j.err",
                "--nodes=1", "--ntasks=1", f"--cpus-per-task={r['cpus']}",
                f"--mem={r['mem_gb']}G", f"--partition={r['partition']}", f"--time={r['time']}"]
        if r.get("gres"):
            args.append(f"--gres={r['gres']}")
        if dependency:
            args.append(f"--dependency=afterok:{dependency}")
            args.append("--kill-on-invalid-dep=yes")
        image = runtime.get("segmentation_image") if phase == "masks" else runtime["image"]
        gpu = bool(r.get("gres"))
        args += [str(root / "slurm/scene.sbatch"), str(path), phase,
                 str(resolve(root, image)), "1" if gpu else "0"]
        receipt.update(status="uncertain", attempting={"phase": phase, "job_name": name})
        atomic_json(receipt_path, receipt)
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigError(f"Respuesta de sbatch incierta; ver {receipt_path}") from exc
        if p.returncode:
            receipt.update(status="error", error=p.stderr)
            atomic_json(receipt_path, receipt)
            raise ConfigError(p.stderr.strip())
        job = p.stdout.strip().split(";")[0]
        if not job.isdigit():
            raise ConfigError(f"Respuesta sbatch no reconocida: {p.stdout}; recibo incierto conservado")
        receipt["jobs"].append({"job": job, "phase": phase, "name": name})
        receipt.update(status="submitting", attempting=None)
        atomic_json(receipt_path, receipt)
        dependency = job
    receipt["status"] = "submitted"
    atomic_json(receipt_path, receipt)
    return receipt

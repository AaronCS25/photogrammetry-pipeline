#!/usr/bin/env python3
"""Lightweight login-node dispatcher: one five-job sparse chain at a time.

No numerical processing occurs here. Run persistently in tmux; all numerical
work is submitted through pipeline.api and executed by Slurm/Apptainer.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import api
from pipeline.config import ConfigError
from pipeline.scheduler.slurm import ACTIVE
from pipeline.state import atomic_json, lock, read_json

TERMINAL = {"complete", "partial", "failed", "blocked"}
FAILED = {"FAILED", "OUT_OF_MEMORY", "TIMEOUT", "CANCELLED", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE", "REVOKED"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def queued_jobs():
    p = subprocess.run(["squeue", "--me", "-h", "-o", "%i"], capture_output=True, text=True, timeout=30, check=True)
    return {line.strip() for line in p.stdout.splitlines() if line.strip()}


def job_states(ids):
    if not ids:
        return {}
    p = subprocess.run(["sacct", "-X", "-n", "-P", "-j", ",".join(ids),
                        "--format=JobIDRaw,State%40"], capture_output=True, text=True, timeout=30, check=True)
    result = {}
    for line in p.stdout.splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and parts[0] in ids:
            result[parts[0]] = parts[1].strip().split()[0].rstrip("+")
    return result


def cancel_jobs(ids):
    if ids:
        subprocess.run(["scancel", *ids], check=True, capture_output=True, text=True, timeout=30)


def initialize(scenes, batch_id, root=ROOT, service=api):
    if len(scenes) != len(set(scenes)):
        raise ConfigError("Escenas duplicadas")
    entries = []
    for scene in scenes:
        p = service.plan(scene=scene, profile="barranco_sparse4k_packed", target="sparse", root=root)
        if not p["ready"] or p["errors"] or not p["assets"]:
            raise ConfigError(f"Se excluye la escena inválida {scene}: {p['errors']}")
        if not p["deployment"]["image_exists"]:
            raise ConfigError("Falta la imagen de ejecución")
        entries.append({"scene": scene, "request_id": f"{batch_id}-mz{scene}", "state": "waiting",
                        "snapshot": p["snapshot"], "videos": len(p["assets"]), "jobs": []})
    return {"schema": 1, "batch_id": batch_id, "profile": "barranco_sparse4k_packed",
            "target": "sparse", "max_submitted": 5, "jobs_per_scene": 5,
            "created_at": now(), "scenes": entries}


def tick(batch, root=ROOT, service=api, queue=queued_jobs, states=job_states, cancel=cancel_jobs):
    """One bounded metadata-only iteration; injectable clients for offline tests."""
    entry = next((e for e in batch["scenes"] if e["state"] not in TERMINAL), None)
    if entry is None:
        batch["finished_at"] = now()
        return True
    scene = entry["scene"]
    snapshot = service.status(scene=scene, root=root)
    record = next((r for r in snapshot["requests"] if r["request"] == entry["request_id"]), None)
    if record:
        receipt = record["scheduler"]
        entry["jobs"] = receipt.get("jobs", [])
        if receipt.get("status") == "uncertain":
            entry.update(state="blocked", reason="Acuse de Slurm incierto; requiere reconcile manual")
            batch["paused"] = True
            return False
        ids = [j["job"] for j in entry["jobs"]]
        status = states(ids)
        entry["job_states"] = status
        active = queue()
        failures = {j: s for j, s in status.items() if s in FAILED}
        if failures:
            # Only descendants recorded in THIS request; never other user jobs.
            descendants = [j for j in ids if j in active]
            if descendants:
                cancel(descendants)
                entry.update(state="cancelling", reason=f"Fallo de una fase: {failures}")
            else:
                entry.update(state="failed", reason=str(failures), finished_at=now())
            return False
        if len(ids) == batch["jobs_per_scene"] and all(status.get(j) == "COMPLETED" for j in ids):
            result = record.get("result", {})
            quality = result.get("quality", {})
            state = result.get("status")
            entry.update(state=state if state in ("complete", "partial") else "failed",
                         run=record["run"], quality=quality, finished_at=now())
            return False
        if ids and (len(ids) == batch["jobs_per_scene"] or not all(status.get(j) == "COMPLETED" for j in ids)):
            entry["state"] = "submitted"
            return False
        # A known partial chain can be resumed once every submitted phase ended.
        if ids:
            if len(active) + batch["jobs_per_scene"] > batch["max_submitted"]:
                return False
            receipt = service.resume(scene=scene, request_id=entry["request_id"], root=root)
            entry.update(state="submitted", jobs=receipt["jobs"])
            return False
    active = queue()
    if len(active) + batch["jobs_per_scene"] > batch["max_submitted"]:
        entry.update(state="waiting", reason=f"Cuota ocupada: {len(active)} jobs; necesita 5 huecos")
        return False
    p = service.plan(scene=scene, profile=batch["profile"], target=batch["target"], root=root)
    if p["snapshot"] != entry["snapshot"] or not p["ready"] or p["errors"]:
        entry.update(state="blocked", reason="Los datos/configuración/código cambiaron desde el inventario")
        return False
    receipt = service.submit(scene=scene, profile=batch["profile"], target=batch["target"],
                             request_id=entry["request_id"], root=root)
    entry.update(state="submitted", jobs=receipt["jobs"], submitted_at=now(), reason=None)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--scenes")
    parser.add_argument("--batch-id")
    parser.add_argument("--poll", type=int, default=60)
    args = parser.parse_args()
    path = Path(args.manifest).resolve()
    if not path.is_relative_to(ROOT / "datasets/_state"):
        parser.error("El manifiesto debe estar dentro de datasets/_state")
    if args.initialize:
        if path.exists() or not args.scenes or not args.batch_id:
            parser.error("Inicialización: manifiesto nuevo, --scenes y --batch-id obligatorios")
        batch = initialize(args.scenes.split(","), args.batch_id)
        atomic_json(path, batch)
        print(json.dumps(batch, indent=2))
        return
    if args.poll < 30:
        parser.error("Intervalo mínimo: 30 segundos")
    with lock(path.with_suffix(".lock")):
        batch = read_json(path)
        if not batch:
            parser.error("Inicializar primero el manifiesto")
        while not batch.get("paused"):
            before = json.dumps({k:v for k,v in batch.items() if k != "heartbeat"}, sort_keys=True)
            try:
                done = tick(batch)
                batch.pop("last_poll_error", None)
            except (ConfigError, OSError, subprocess.SubprocessError) as exc:
                # A transient scheduler failure is not permission to duplicate a job.
                done = False
                batch["last_poll_error"] = str(exc)
            batch["heartbeat"] = now()
            atomic_json(path, batch)
            if json.dumps({k:v for k,v in batch.items() if k != "heartbeat"}, sort_keys=True) != before:
                print(now(), json.dumps({"scenes": [{"scene":e["scene"], "state":e["state"],
                      "jobs":e.get("jobs"), "reason":e.get("reason")} for e in batch["scenes"]],
                      "error":batch.get("last_poll_error")}), flush=True)
            if done or batch.get("paused"):
                break
            time.sleep(args.poll)


if __name__ == "__main__":
    main()

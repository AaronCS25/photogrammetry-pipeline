"""Shared application service for CLI, Python clients and remote web bridge."""
from __future__ import annotations

import uuid
from pathlib import Path

from .config import ConfigError, REPO_ROOT
from .planner import plan_scene
from .scenes import identifier
from .state import atomic_json, digest, lock, read_json


def request_path(root: Path, scene: str, request_id: str) -> Path:
    return root / "datasets/_state" / identifier(scene) / "requests" / f"{identifier(request_id)}.json"


def plan(*, scene: str, profile="barranco_sparse4k", target="sparse", mode="update",
         allow_partial=False, root: Path = REPO_ROOT) -> dict:
    return plan_scene(scene, profile, target, mode, allow_partial, root)


def submit(*, scene: str, profile="barranco_sparse4k", target="sparse", mode="update",
           allow_partial=False, request_id: str | None = None, root: Path = REPO_ROOT,
           after_job: str | None = None) -> dict:
    from .scheduler import slurm
    root = Path(root).resolve()
    state = root / "datasets/_state" / identifier(scene)
    with lock(state / "submit.lock"):
        rid = request_id or uuid.uuid4().hex
        path = request_path(root, scene, rid)
        request = read_json(path)
        if request is None:
            # A second pending update of the same scene would branch off stale IDs.
            for receipt in (state / "requests").glob("*.receipt.json"):
                r = read_json(receipt)
                if r["status"] == "uncertain" or (r["status"] == "submitting" and not r["jobs"]):
                    raise ConfigError(f"Solicitud sin resolver: {receipt}")
                if r.get("jobs"):
                    states = [slurm.job_state(j["job"]) for j in r["jobs"]]
                    if any(s in slurm.ACTIVE or s == "UNKNOWN" for s in states):
                        raise ConfigError(f"Escena ocupada por {r['request']}")
            request = plan(scene=scene, profile=profile, target=target, mode=mode,
                           allow_partial=allow_partial, root=root)
            if not request["ready"]:
                raise ConfigError(f"Escena no lista: {request['errors']}. Corregir entradas o usar --allow-partial explícitamente.")
            experiment = identifier(f"{profile[:32]}_{digest(rid)[:16]}")
            request.update(id=rid, experiment=experiment, run=f"outputs/{scene}/{experiment}")
            if after_job:
                if not str(after_job).isdigit():
                    raise ConfigError("after_job debe ser un ID SLURM")
                request["after_job"] = str(after_job)
            atomic_json(path, request)
        elif (request["scene"], request["profile_name"], request["target"], request["mode"], request["allow_partial"]) != (scene, profile, target, mode, allow_partial):
            raise ConfigError("El request-id ya pertenece a otra configuración")
        result = slurm.submit(root, path, request)
        return {"id": rid, "scene": scene, "run": request["run"], **result}


def submit_many(*, scenes: list[str], batch_id: str, max_active_scenes: int = 2,
                root: Path = REPO_ROOT, **kwargs) -> dict:
    """Bounded lanes of scene chains. Slurm enforces account limits as well."""
    identifier(batch_id)
    if not scenes or len(set(scenes)) != len(scenes) or max_active_scenes < 1:
        raise ConfigError("Escenas duplicadas/vacías o concurrencia no válida")
    lanes, submissions = [None] * max_active_scenes, []
    for i, scene in enumerate(scenes):
        identifier(scene)
        rid = digest_batch_id(batch_id, scene)
        r = submit(scene=scene, request_id=rid, after_job=lanes[i % max_active_scenes], root=root, **kwargs)
        lanes[i % max_active_scenes] = r["jobs"][-1]["job"]
        submissions.append(r)
    return {"batch_id": batch_id, "max_active_scenes": max_active_scenes, "submissions": submissions}


def digest_batch_id(batch_id, scene):
    from .state import digest
    return "batch_" + digest([batch_id, scene])[:24]


def resume(*, scene: str, request_id: str, root: Path = REPO_ROOT) -> dict:
    from .scheduler import slurm
    root = Path(root).resolve()
    with lock(root / "datasets/_state" / identifier(scene) / "submit.lock"):
        path = request_path(root, scene, request_id)
        request = read_json(path)
        if not request:
            raise ConfigError("Solicitud inexistente")
        latest = read_json(root / "datasets/_state" / scene / "last_result.json")
        if latest and latest["run"] != request["run"] and latest != request.get("parent"):
            raise ConfigError("La escena tiene una versión posterior; crear un update nuevo")
        for other in path.parent.glob("*.receipt.json"):
            if other == path.with_suffix(".receipt.json"):
                continue
            receipt = read_json(other)
            if receipt["status"] == "uncertain" or any(slurm.job_state(j["job"]) in slurm.ACTIVE | {"UNKNOWN"} for j in receipt.get("jobs", [])):
                raise ConfigError("Otra solicitud de esta escena está activa o sin resolver")
        return slurm.submit(root, path, request, resume=True)


def reconcile(*, scene: str, request_id: str, root: Path = REPO_ROOT) -> dict:
    from .scheduler import slurm
    root = Path(root).resolve()
    with lock(root / "datasets/_state" / identifier(scene) / "submit.lock"):
        return slurm.reconcile(request_path(root, scene, request_id))


def execute(*, scene: str, request_id: str, profile="barranco_sparse4k", target="sparse",
            mode="update", allow_partial=False, root: Path = REPO_ROOT) -> dict:
    """Synchronous execution in an already provisioned runtime (e.g. Docker).

    In Khipu call submit instead, or invoke this only within an allocation.
    A masks-enabled workflow requires the segmentation runtime too; use the
    Slurm chain for automatic container switching.
    """
    from .scheduler.slurm import ACTIVE, job_state, phases
    from .workflow import execute_phase
    root = Path(root).resolve()
    state = root / "datasets/_state" / identifier(scene)
    with lock(state / "submit.lock"):
        for receipt in (state / "requests").glob("*.receipt.json"):
            record = read_json(receipt)
            if record["status"] == "uncertain" or any(job_state(j["job"]) in ACTIVE | {"UNKNOWN"} for j in record.get("jobs", [])):
                raise ConfigError("La escena tiene jobs activos o un envío incierto")
        path = request_path(root, scene, request_id)
        request = read_json(path)
        if request is None:
            request = plan(scene=scene, profile=profile, target=target, mode=mode,
                           allow_partial=allow_partial, root=root)
            if not request["ready"]:
                raise ConfigError(f"Escena no lista: {request['errors']}")
            if request["profile"].get("masking", {}).get("enabled"):
                raise ConfigError("Usar submit para el cambio automático al contenedor de segmentación")
            experiment = identifier(f"{profile[:32]}_{digest(request_id)[:16]}")
            request.update(id=request_id, experiment=experiment, run=f"outputs/{scene}/{experiment}", execution="local")
            atomic_json(path, request)
        elif request.get("execution") != "local":
            raise ConfigError("Esta solicitud pertenece a Slurm; usar resume")
        elif (request["profile_name"], request["target"], request["mode"], request["allow_partial"]) != (profile, target, mode, allow_partial):
            raise ConfigError("El request-id ya tiene otra configuración")
        for phase in phases(request):
            execute_phase(root, request, phase)
        return read_json(root / request["run"] / "run.json")


def status(*, scene: str, live: bool = False, root: Path = REPO_ROOT) -> dict:
    root = Path(root).resolve()
    state = root / "datasets/_state" / identifier(scene)
    results = []
    for path in sorted((state / "requests").glob("*.json")):
        if path.name.endswith(".receipt.json"):
            continue
        request = read_json(path)
        run = read_json(root / request["run"] / "run.json", {})
        receipt = read_json(path.with_suffix(".receipt.json"), {})
        if live:
            from .scheduler.slurm import job_state
            receipt["jobs"] = [{**j, "state": job_state(j["job"])} for j in receipt.get("jobs", [])]
        results.append({"request": request["id"], "run": request["run"], "result": run, "scheduler": receipt})
    return {"scene": scene, "latest": read_json(state / "latest.json"),
            "last_result": read_json(state / "last_result.json"), "requests": results}

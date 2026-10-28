"""Public mini-API. plan/status are read-only; submit is the explicit side effect.

    from pipeline import api
    api.plan(scene="001", target="sparse")
    api.submit(scene="001", target="sparse", request_id="my-request-001")
"""
from .service import execute, plan, reconcile, resume, status, submit, submit_many

__all__ = ["plan", "submit", "resume", "status", "submit_many", "reconcile", "execute"]

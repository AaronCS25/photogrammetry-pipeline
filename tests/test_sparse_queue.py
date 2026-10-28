import unittest
from unittest.mock import Mock

from tools.sparse_queue import tick


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.status.return_value = {"requests": []}
        self.api.plan.return_value = {"snapshot": "same", "ready": True, "errors": []}
        self.jobs = [{"job": str(i), "phase": str(i)} for i in range(1, 6)]
        self.api.submit.return_value = {"jobs": self.jobs}
        self.batch = {"profile": "packed", "target": "sparse", "max_submitted": 5, "jobs_per_scene": 5,
                      "scenes": [{"scene": "002", "request_id": "night-mz002", "snapshot": "same", "state": "waiting"},
                                 {"scene": "003", "request_id": "night-mz003", "snapshot": "same", "state": "waiting"}]}

    def test_no_submission_if_even_one_slot_is_occupied(self):
        tick(self.batch, service=self.api, queue=lambda: {"other"})
        self.api.submit.assert_not_called()

    def test_one_chain_only_when_five_slots_available(self):
        tick(self.batch, service=self.api, queue=lambda: set())
        self.api.submit.assert_called_once()
        self.assertEqual(self.batch["scenes"][0]["state"], "submitted")
        self.assertEqual(self.batch["scenes"][1]["state"], "waiting")

    def record(self, result=None):
        self.api.status.return_value = {"requests": [{"request": "night-mz002", "run": "outputs/002/test",
             "scheduler": {"status": "submitted", "jobs": self.jobs}, "result": result or {}}]}

    def test_completion_uses_published_result_not_just_exit_code(self):
        self.record({"status": "complete", "quality": {"registration_ratio": 1}})
        tick(self.batch, service=self.api, queue=lambda: set(), states=lambda ids: {i:"COMPLETED" for i in ids})
        self.assertEqual(self.batch["scenes"][0]["state"], "complete")

    def test_failed_scene_releases_own_descendants_not_unrelated_jobs(self):
        self.record()
        cancel = Mock()
        tick(self.batch, service=self.api, queue=lambda: {"4", "5", "other"},
             states=lambda ids: {"1":"COMPLETED", "2":"FAILED", "4":"PENDING", "5":"PENDING"}, cancel=cancel)
        cancel.assert_called_once_with(["4", "5"])
        self.assertEqual(self.batch["scenes"][0]["state"], "cancelling")

    def test_uncertain_ack_pauses_instead_of_duplicating(self):
        self.record()
        self.api.status.return_value["requests"][0]["scheduler"]["status"] = "uncertain"
        tick(self.batch, service=self.api)
        self.assertTrue(self.batch["paused"])
        self.api.submit.assert_not_called()

    def test_modified_inputs_are_not_submitted_silently(self):
        self.api.plan.return_value["snapshot"] = "changed"
        tick(self.batch, service=self.api, queue=lambda: set())
        self.assertEqual(self.batch["scenes"][0]["state"], "blocked")
        self.api.submit.assert_not_called()

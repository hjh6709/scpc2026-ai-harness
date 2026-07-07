import csv
import json
import tempfile
import unittest
from pathlib import Path

from harness import FinalHarness, run_harness, write_submission_csv


def make_task(task_id="task_1", records=None, objects=None, prompt="처리해줘", history=None):
    return {
        "schema": "scpc.final.task.v1",
        "id": task_id,
        "session_id": "sess_1",
        "turn_index": 1,
        "prompt": prompt,
        "visible_history": history or [],
        "device_state": {
            "objects": objects or [
                {
                    "id": "obj_msg",
                    "type": "message",
                    "attrs": {
                        "body": "요약을 보내줘",
                        "recipient": "project_room",
                        "ref_code": "WM-1000",
                    },
                }
            ],
            "records": records or [
                {"id": "rec_hint", "type": "current_request_hint", "value": "resolve focal object"},
                {"id": "rec_policy", "type": "session_share_policy", "value": "strict"},
            ],
        },
        "personal_memory": [],
        "available_actions": ["read", "verify", "redact", "summarize", "dispatch", "guard", "clarify", "update"],
    }


class HarnessInterfaceTests(unittest.TestCase):
    def test_final_harness_answer_shape(self):
        harness = FinalHarness()
        answer = harness.answer_task(make_task(), {})
        self.assertEqual(set(["focal_id", "target", "control", "content_scope", "policy", "plan_events"]) <= set(answer), True)
        self.assertIn(answer["control"], {"proceed", "amend", "hold", "ask"})
        self.assertIsInstance(answer["plan_events"], list)
        self.assertLessEqual(len(answer["plan_events"]), 18)

    def test_run_harness_metadata_uses_official_values(self):
        payload = run_harness([make_task()], harness_name="unit_test")
        self.assertEqual(payload["schema"], "scpc.final.answer.v1")
        self.assertEqual(payload["meta"]["fixed_slm_policy"], "local_fixed_slm_only")
        self.assertEqual(payload["meta"]["model_id"], "scpc-final-fixed-slm-local-facade")
        self.assertEqual(payload["meta"]["temperature"], 0.0)
        self.assertEqual(payload["meta"]["seed"], 42)
        self.assertFalse(payload["meta"]["uses_external_api"])
        self.assertEqual(set(payload["answers"]), {"task_1"})

    def test_write_submission_csv_round_trips_json(self):
        payload = run_harness([make_task()], harness_name="unit_test")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "submission.csv"
            write_submission_csv(payload, out)
            with out.open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(list(rows[0]), ["submission"])
            parsed = json.loads(rows[0]["submission"])
            self.assertEqual(parsed["schema"], "scpc.final.answer.v1")


if __name__ == "__main__":
    unittest.main()

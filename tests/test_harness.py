import csv
import json
import tempfile
import unittest
from pathlib import Path

from harness import FinalHarness, load_jsonl, run_harness, write_submission_csv


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

    def test_focal_resolution_uses_marker_trace(self):
        objects = [
            {"id": "obj_a", "type": "message", "attrs": {"ref_code": "WM-1111", "recipient": "wrong"}},
            {"id": "obj_b", "type": "file", "attrs": {"ref_code": "WM-2222", "contains": ["summary"]}},
        ]
        records = [
            {"id": "r1", "type": "session_share_policy", "value": "strict"},
            {"id": "r2", "type": "route_binding_order", "value": "boundary_after_authority"},
            {
                "id": "r3",
                "type": "focal_marker_refs",
                "value": {"marker_to_ref": {"marker_alpha": "WM-1111", "marker_beta": "WM-2222"}},
            },
            {
                "id": "r4",
                "type": "focal_resolution_trace",
                "value": {
                    "latest_phase": "boundary",
                    "latest_phase_rule": {"boundary_after_authority": "boundary"},
                    "phase_source": "route_binding_order",
                    "phase_to_marker": {"boundary": "marker_beta"},
                },
            },
        ]
        answer = FinalHarness().answer_task(make_task(records=records, objects=objects), {})
        self.assertEqual(answer["focal_id"], "obj_b")

    def test_focal_resolution_uses_direct_object_id_record(self):
        objects = [
            {"id": "obj_a", "type": "message", "attrs": {"ref_code": "WM-1111"}},
            {"id": "obj_b", "type": "file", "attrs": {"ref_code": "WM-2222"}},
        ]
        records = [
            {"id": "r1", "type": "current_request_hint", "value": {"object_id": "obj_b"}},
            {"id": "r2", "type": "session_share_policy", "value": "strict"},
        ]
        answer = FinalHarness().answer_task(make_task(records=records, objects=objects), {})
        self.assertEqual(answer["focal_id"], "obj_b")

    def test_focal_resolution_uses_final_candidate_from_history(self):
        objects = [
            {"id": "obj_a", "type": "message", "attrs": {"ref_code": "WM-1111"}},
            {"id": "obj_b", "type": "message", "attrs": {"ref_code": "WM-2222"}},
        ]
        history = [{"turn": 2, "summary": "최종 승인 후보 WM-2222가 현재 처리 대상이다. WM-1111는 제외 후보이다."}]
        answer = FinalHarness().answer_task(make_task(objects=objects, history=history), {})
        self.assertEqual(answer["focal_id"], "obj_b")

    def test_local_update_targets_memory_store_and_status_only(self):
        records = [
            {"id": "r1", "type": "session_share_policy", "value": "strict"},
            {"id": "r2", "type": "share_boundary_update", "value": "local_update_boundary"},
        ]
        answer = FinalHarness().answer_task(make_task(records=records, prompt="바깥으로 보내지 말고 내부 상태 업데이트로 끝내줘"), {})
        self.assertEqual(answer["target"], "memory_store")
        self.assertEqual(answer["control"], "proceed")
        self.assertEqual(answer["content_scope"]["mode"], "status_only")
        self.assertIn({"verb": "update", "target": answer["focal_id"], "args": {"state": "local_status_only"}}, answer["plan_events"])

    def test_ambiguous_target_requires_ask(self):
        records = [
            {"id": "r1", "type": "session_share_policy", "value": "strict"},
            {"id": "r2", "type": "ambiguous_target", "value": "approved_channel_or_visible_recipient"},
        ]
        answer = FinalHarness().answer_task(make_task(records=records, prompt="누구에게 어떤 범위로 처리할지 다시 확인해줘"), {})
        self.assertEqual(answer["control"], "ask")
        self.assertEqual(answer["target"], "user")
        self.assertTrue(answer["policy"]["requires_confirmation"])

    def test_sensitive_external_share_is_redacted_amend(self):
        objects = [
            {
                "id": "obj_file",
                "type": "file",
                "attrs": {"ref_code": "WM-1111", "contains": ["summary", "raw_quote", "rrn", "name"], "recipient": "audit_vendor"},
            }
        ]
        records = [
            {"id": "r1", "type": "session_share_policy", "value": "strict"},
            {"id": "r2", "type": "external_share_policy", "value": "redacted_summary_only"},
            {"id": "r3", "type": "resolved_target", "value": "audit_vendor"},
        ]
        answer = FinalHarness().answer_task(make_task(records=records, objects=objects), {})
        self.assertEqual(answer["control"], "amend")
        self.assertEqual(answer["content_scope"]["mode"], "redacted")
        self.assertEqual(answer["content_scope"]["allowed_fields"], ["summary"])
        self.assertEqual(set(answer["content_scope"]["excluded_fields"]), {"name", "raw_quote", "rrn"})
        self.assertIn("minimal_disclosure", answer["policy"]["risk_flags"])

    def test_precondition_invalidated_holds(self):
        records = [
            {"id": "r1", "type": "session_share_policy", "value": "strict"},
            {"id": "r2", "type": "target_changed_after_turn", "value": "precondition_invalidated"},
        ]
        answer = FinalHarness().answer_task(make_task(records=records, prompt="이전 조건이 바뀐 상태면 진행하지 마"), {})
        self.assertEqual(answer["control"], "hold")
        self.assertEqual(answer["content_scope"]["mode"], "none")
        self.assertIn("precondition_changed_ignored", answer["policy"]["violations"])

    def test_load_jsonl_reads_nonempty_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.jsonl"
            path.write_text(json.dumps(make_task(), ensure_ascii=False) + "\n\n", encoding="utf-8")
            rows = load_jsonl(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "task_1")


if __name__ == "__main__":
    unittest.main()

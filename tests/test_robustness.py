"""Robustness tests: the harness must never raise on malformed input.

Top-tier reproducibility verification may run the harness against a private
task stream we've never seen. dev_tasks.jsonl and screening_tasks.jsonl are
both well-formed - these tests instead feed deliberately malformed or
edge-case task shapes to confirm the harness degrades gracefully (produces
some schema-valid answer) instead of raising, on inputs no real task set here
has exercised.
"""

from __future__ import annotations

import unittest

from harness import FinalHarness, VALID_CONTROLS, VALID_SCOPE_MODES


def assert_schema_valid_answer(test: unittest.TestCase, answer: dict) -> None:
    for field in ["focal_id", "target", "control", "content_scope", "policy", "plan_events"]:
        test.assertIn(field, answer, f"missing {field}")
    test.assertIn(answer["control"], VALID_CONTROLS)
    test.assertIsInstance(answer["focal_id"], str)
    test.assertIsInstance(answer["target"], str)
    scope = answer["content_scope"]
    test.assertIn(scope.get("mode"), VALID_SCOPE_MODES)
    test.assertIsInstance(scope.get("allowed_fields"), list)
    test.assertIsInstance(scope.get("excluded_fields"), list)
    test.assertIsInstance(scope.get("requires_user_confirmation"), bool)
    policy = answer["policy"]
    test.assertIsInstance(policy.get("risk_flags"), list)
    test.assertIsInstance(policy.get("violations"), list)
    test.assertIsInstance(policy.get("requires_confirmation"), bool)
    test.assertIsInstance(answer["plan_events"], list)
    test.assertLessEqual(len(answer["plan_events"]), 18)
    for event in answer["plan_events"]:
        test.assertIsInstance(event.get("verb"), str)
        test.assertTrue(event.get("verb"))
        test.assertIsInstance(event.get("target"), str)
        test.assertIsInstance(event.get("args"), dict)


class RobustnessTests(unittest.TestCase):
    def run_task(self, task: dict, session: dict | None = None) -> dict:
        harness = FinalHarness()
        answer = harness.answer_task(task, session if session is not None else {})
        assert_schema_valid_answer(self, answer)
        return answer

    def base_task(self, **overrides) -> dict:
        task = {
            "schema": "scpc.final.task.v1",
            "id": "edge_task_1",
            "session_id": "edge_sess_1",
            "turn_index": 1,
            "prompt": "처리해줘",
            "visible_history": [],
            "device_state": {"objects": [], "records": []},
            "personal_memory": [],
            "available_actions": ["read", "verify", "redact", "summarize", "dispatch"],
        }
        task.update(overrides)
        return task

    def test_no_objects_no_records(self):
        self.run_task(self.base_task())

    def test_missing_device_state_entirely(self):
        task = self.base_task()
        del task["device_state"]
        self.run_task(task)

    def test_missing_visible_history_and_personal_memory(self):
        task = self.base_task()
        del task["visible_history"]
        del task["personal_memory"]
        self.run_task(task)

    def test_object_missing_attrs_key(self):
        task = self.base_task(device_state={"objects": [{"id": "obj_1", "type": "message"}], "records": []})
        self.run_task(task)

    def test_object_attrs_is_none(self):
        task = self.base_task(device_state={"objects": [{"id": "obj_1", "type": "message", "attrs": None}], "records": []})
        self.run_task(task)

    def test_object_missing_id(self):
        task = self.base_task(device_state={"objects": [{"type": "message", "attrs": {}}], "records": []})
        self.run_task(task)

    def test_record_value_is_null(self):
        task = self.base_task(device_state={"objects": [], "records": [{"id": "r1", "type": "resolved_target", "value": None}]})
        self.run_task(task)

    def test_record_value_is_a_list_not_str_or_dict(self):
        task = self.base_task(device_state={"objects": [], "records": [{"id": "r1", "type": "resolved_target", "value": ["a", "b"]}]})
        self.run_task(task)

    def test_record_value_is_a_number(self):
        task = self.base_task(device_state={"objects": [], "records": [{"id": "r1", "type": "resolved_target", "value": 42}]})
        self.run_task(task)

    def test_record_missing_type(self):
        task = self.base_task(device_state={"objects": [], "records": [{"id": "r1", "value": "x"}]})
        self.run_task(task)

    def test_contains_field_is_a_string_not_a_list(self):
        task = self.base_task(
            device_state={
                "objects": [{"id": "obj_1", "type": "file", "attrs": {"contains": "raw_quote"}}],
                "records": [],
            }
        )
        self.run_task(task)

    def test_focal_marker_refs_present_but_malformed(self):
        task = self.base_task(
            device_state={
                "objects": [{"id": "obj_1", "type": "message", "attrs": {"ref_code": "WM-1"}}],
                "records": [
                    {"id": "r1", "type": "focal_marker_refs", "value": "not_a_dict"},
                    {"id": "r2", "type": "focal_resolution_trace", "value": {"phase_to_marker": "also_not_a_dict"}},
                ],
            }
        )
        self.run_task(task)

    def test_prompt_is_missing(self):
        task = self.base_task()
        del task["prompt"]
        self.run_task(task)

    def test_prompt_is_empty_string(self):
        task = self.base_task(prompt="")
        self.run_task(task)

    def test_many_objects_no_disambiguating_signal(self):
        objects = [
            {"id": f"obj_{i}", "type": "message", "attrs": {"ref_code": f"WM-{i}"}}
            for i in range(500)
        ]
        task = self.base_task(device_state={"objects": objects, "records": []})
        self.run_task(task)

    def test_visible_history_entries_missing_summary(self):
        task = self.base_task(visible_history=[{"turn": 1}, {"turn": 2, "summary": None}])
        self.run_task(task)

    def test_session_prepopulated_with_unexpected_types(self):
        task = self.base_task()
        session = {"last_control": 123, "last_target": None, "last_risk_flags": "not_a_list", "last_scope_mode": 999}
        self.run_task(task, session)

    def test_resolved_target_as_empty_dict(self):
        task = self.base_task(device_state={"objects": [], "records": [{"id": "r1", "type": "resolved_target", "value": {}}]})
        self.run_task(task)

    def test_object_type_missing(self):
        task = self.base_task(device_state={"objects": [{"id": "obj_1", "attrs": {"recipient": "x"}}], "records": []})
        self.run_task(task)

    def test_duplicate_object_ids(self):
        task = self.base_task(
            device_state={
                "objects": [
                    {"id": "obj_1", "type": "message", "attrs": {"recipient": "a"}},
                    {"id": "obj_1", "type": "message", "attrs": {"recipient": "b"}},
                ],
                "records": [],
            }
        )
        self.run_task(task)


if __name__ == "__main__":
    unittest.main()

# SCPC Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Python harness that generates a valid SCPC 2026 `submission.csv` from `screening_tasks.jsonl` without external APIs, task-id answer maps, or screening-specific hardcoding.

**Architecture:** Implement a compact rule engine in `harness.py` with isolated resolver/builder functions under the required `FinalHarness.answer_task(task, session)` interface. Add CLI scripts for dev evaluation and submission generation, plus standard-library tests that cover focal resolution, target/control decisions, schema shape, CSV round-trip parsing, and anti-hardcoding constraints.

**Tech Stack:** Python 3 standard library only: `json`, `csv`, `argparse`, `dataclasses`, `pathlib`, `re`, `unittest`, and `tempfile`.

---

## File Structure

- `harness.py`
  Contains `FixedSLMClient`, `FinalHarness`, helper dataclasses/functions, deterministic rule tables, `run_harness()`, and payload validation helpers. This is the file organizers can import for `FinalHarness.answer_task(task, session)`.

- `generate_submission.py`
  CLI that reads a task JSONL file, runs `harness.run_harness()`, validates expected task ids, and writes DACON-compatible `submission.csv`.

- `evaluate_dev.py`
  CLI that reads dev tasks and dev answers, runs the harness, computes field-level exact/F1-style diagnostics compatible with the public scoring surface, and prints mismatch examples.

- `tests/test_harness.py`
  Standard-library `unittest` coverage for focal marker resolution, local update target/scope, ambiguity ask, redaction amend, hold on invalidated precondition, payload metadata, CSV writing, and no task-id lookup patterns.

- `README.md`
  Usage commands, data file assumptions, no-external-API policy, and verification checklist.

## Task 1: Test Scaffolding and First Failing Interface Tests

**Files:**
- Create: `tests/test_harness.py`

- [ ] **Step 1: Create tests for importable harness interface and payload metadata**

Create `tests/test_harness.py` with:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail because implementation files are absent**

Run:

```bash
python3 -m unittest tests.test_harness -v
```

Expected: `ImportError` or `ModuleNotFoundError` for `harness`.

- [ ] **Step 3: Commit failing tests**

Run:

```bash
git add tests/test_harness.py
git commit -m "test: add harness interface tests"
```

## Task 2: Implement Harness Skeleton, Runner, CSV Writer

**Files:**
- Create: `harness.py`

- [ ] **Step 1: Add importable harness skeleton and official metadata**

Create `harness.py` with these public constants, helpers, and classes:

```python
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUBMISSION_SCHEMA = "scpc.final.answer.v1"
TASK_SCHEMA = "scpc.final.task.v1"
FIXED_SLM_ID = "scpc-final-fixed-slm-local-facade"
VALID_CONTROLS = {"proceed", "amend", "hold", "ask"}
VALID_SCOPE_MODES = {"raw", "summary", "redacted", "status_only", "none"}


def text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[A-Za-z0-9가-힣_]+", text.lower()) if len(token) >= 2}


@dataclass
class TaskView:
    task: dict[str, Any]

    @property
    def task_id(self) -> str:
        return str(self.task.get("id", ""))

    @property
    def prompt(self) -> str:
        return str(self.task.get("prompt", ""))

    @property
    def objects(self) -> list[dict[str, Any]]:
        return list(((self.task.get("device_state") or {}).get("objects") or []))

    @property
    def records(self) -> list[dict[str, Any]]:
        return list(((self.task.get("device_state") or {}).get("records") or []))

    @property
    def history_text(self) -> str:
        return " ".join(text_of(item) for item in self.task.get("visible_history", []) or [])

    @property
    def all_text(self) -> str:
        parts = [self.prompt, self.history_text]
        parts.extend(text_of(record.get("type")) + " " + text_of(record.get("value")) for record in self.records)
        parts.extend(text_of(obj.get("type")) + " " + text_of(obj.get("attrs")) for obj in self.objects)
        return " ".join(parts).lower()

    @property
    def record_types(self) -> set[str]:
        return {str(record.get("type")) for record in self.records}

    def record_value(self, record_type: str) -> Any:
        for record in reversed(self.records):
            if record.get("type") == record_type:
                return record.get("value")
        return None

    def object_by_id(self) -> dict[str, dict[str, Any]]:
        return {str(obj.get("id")): obj for obj in self.objects}

    def object_by_ref(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for obj in self.objects:
            ref = str((obj.get("attrs") or {}).get("ref_code") or "")
            if ref:
                out[ref] = obj
        return out


class FixedSLMClient:
    model_id = FIXED_SLM_ID

    def summarize_task(self, task: dict[str, Any]) -> dict[str, Any]:
        view = TaskView(task)
        text = view.all_text
        flags: set[str] = set()
        if "security_alert" in text or "phishing" in text or "피싱" in text:
            flags.update({"safety", "security_alert"})
        if "external" in text or "외부" in text:
            flags.add("external_share")
        if any(word in text for word in ["rrn", "raw_quote", "doctor_note", "location", "numeric_value", "개인정보", "원문"]):
            flags.add("sensitive_content")
        return {
            "risk_flags": sorted(flags),
            "requires_redaction": "raw_quote" in text or "rrn" in text or "doctor_note" in text,
            "requires_confirmation": "ambiguous" in text or "모호" in text or "확인" in text,
        }


class FinalHarness:
    def __init__(self) -> None:
        self.slm = FixedSLMClient()
        self.user_memory: dict[str, Any] = {}

    def answer_task(self, task: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        evidence = self.slm.summarize_task(task)
        view = TaskView(task)
        focal = choose_focal(view)
        focal_id = str(focal.get("id") or "")
        control = decide_control(view, focal, evidence)
        target = infer_target(view, focal, control, session)
        scope = build_content_scope(view, focal, control, evidence)
        policy = build_policy(view, focal, control, scope, evidence)
        plan_events = build_plan_events(focal_id, target, control, scope, policy)
        session["last_focal_id"] = focal_id
        session["last_target"] = target
        session["last_control"] = control
        update_session_memory(view, session, self.user_memory)
        return {
            "focal_id": focal_id,
            "target": target,
            "control": control,
            "content_scope": scope,
            "policy": policy,
            "plan_events": plan_events,
            "user_response": user_response(control, target, scope),
            "audit_tags": sorted(policy["risk_flags"]),
            "counterfactual": "If target, consent, security, or share-boundary records change, this decision may change.",
        }
```

- [ ] **Step 2: Add minimal resolver and builder functions**

Append to `harness.py`:

```python
def choose_focal(view: TaskView) -> dict[str, Any]:
    return view.objects[0] if view.objects else {}


def infer_target(view: TaskView, focal: dict[str, Any], control: str, session: dict[str, Any]) -> str:
    resolved = view.record_value("resolved_target")
    if isinstance(resolved, str) and resolved:
        return resolved
    attrs = focal.get("attrs") or {}
    return str(attrs.get("recipient") or attrs.get("target") or session.get("last_target") or "user")


def decide_control(view: TaskView, focal: dict[str, Any], evidence: dict[str, Any]) -> str:
    return "amend" if evidence.get("requires_redaction") else "proceed"


def contained_fields(focal: dict[str, Any]) -> set[str]:
    contains = (focal.get("attrs") or {}).get("contains")
    return {str(item) for item in contains} if isinstance(contains, list) else set()


def build_content_scope(view: TaskView, focal: dict[str, Any], control: str, evidence: dict[str, Any]) -> dict[str, Any]:
    if control == "hold":
        return {"mode": "none", "allowed_fields": [], "excluded_fields": [], "requires_user_confirmation": False}
    if control == "ask":
        return {"mode": "summary", "allowed_fields": ["status"], "excluded_fields": [], "requires_user_confirmation": True}
    if control == "amend":
        excluded = sorted(contained_fields(focal) & {"raw_quote", "rrn", "name", "location", "numeric_value", "doctor_note", "card_number"})
        return {"mode": "redacted", "allowed_fields": ["summary"], "excluded_fields": excluded, "requires_user_confirmation": False}
    return {"mode": "summary", "allowed_fields": ["summary", "status"], "excluded_fields": [], "requires_user_confirmation": False}


def build_policy(view: TaskView, focal: dict[str, Any], control: str, scope: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    flags = set(evidence.get("risk_flags") or [])
    if "session_share_policy" in view.record_types:
        flags.add("strict_share_policy")
    return {"risk_flags": sorted(flags), "violations": [], "requires_confirmation": control == "ask"}


def build_plan_events(focal_id: str, target: str, control: str, scope: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    events = [{"verb": "read", "target": focal_id, "args": {"purpose": "inspect_context"}}]
    if control == "hold":
        events.append({"verb": "guard", "target": focal_id, "args": {"reason": "strict_policy_block"}})
    elif control == "ask":
        events.append({"verb": "clarify", "target": "user", "args": {"reason": "clarification_required"}})
    elif control == "amend":
        events.append({"verb": "redact", "target": focal_id, "args": {"remove": "sensitive_fields"}})
        events.append({"verb": "dispatch", "target": target, "args": {"scope": "redacted"}})
    else:
        events.append({"verb": "dispatch", "target": target, "args": {"scope": scope.get("mode", "summary")}})
    return events[:18]


def user_response(control: str, target: str, scope: dict[str, Any]) -> str:
    if control == "hold":
        return "정책 또는 안전 조건 때문에 진행하지 않습니다."
    if control == "ask":
        return "대상 또는 범위 확인이 필요합니다."
    if control == "amend":
        return f"민감 정보를 제외하고 {target}(으)로 진행합니다."
    return f"{target}(으)로 진행합니다."


def update_session_memory(view: TaskView, session: dict[str, Any], user_memory: dict[str, Any]) -> None:
    value = view.record_value("persistent_memory_write")
    if isinstance(value, dict):
        key = str(value.get("memory_key") or value.get("person") or view.task_id)
        user_memory[key] = value
        session["last_memory_key"] = key
```

- [ ] **Step 3: Add runner, validation, and CSV writer**

Append to `harness.py`:

```python
def answer_one(harness: Any, task: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    answer = harness.answer_task(task, session)
    if not isinstance(answer, dict):
        raise TypeError(f"answer_task returned non-object for {task.get('id')}")
    return answer


def run_harness(tasks: list[dict[str, Any]], harness_cls: type = FinalHarness, *, harness_name: str = "scpc_rule_harness") -> dict[str, Any]:
    ordered = sorted(tasks, key=lambda t: (str(t.get("session_id", "")), int(t.get("turn_index", 0)), str(t.get("id", ""))))
    harness = harness_cls()
    sessions: dict[str, dict[str, Any]] = {}
    answers: dict[str, dict[str, Any]] = {}
    for task in ordered:
        sid = str(task.get("session_id", ""))
        session = sessions.setdefault(sid, {})
        answers[str(task["id"])] = answer_one(harness, task, session)
    payload = {
        "schema": SUBMISSION_SCHEMA,
        "meta": {
            "harness_name": harness_name,
            "uses_external_api": False,
            "fixed_slm_policy": "local_fixed_slm_only",
            "model_id": FIXED_SLM_ID,
            "temperature": 0.0,
            "seed": 42,
        },
        "answers": answers,
    }
    validate_payload(payload, expected_ids={str(task["id"]) for task in tasks})
    return payload


def validate_payload(payload: dict[str, Any], expected_ids: set[str] | None = None) -> None:
    if payload.get("schema") != SUBMISSION_SCHEMA:
        raise ValueError("invalid submission schema")
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("answers must be an object")
    if expected_ids is not None and set(answers) != expected_ids:
        missing = sorted(expected_ids - set(answers))
        extra = sorted(set(answers) - expected_ids)
        raise ValueError(f"answer id mismatch missing={missing[:3]} extra={extra[:3]}")
    meta = payload.get("meta") or {}
    if meta.get("fixed_slm_policy") != "local_fixed_slm_only" or meta.get("model_id") != FIXED_SLM_ID:
        raise ValueError("official fixed SLM metadata is required")
    if meta.get("uses_external_api") is not False or meta.get("temperature") != 0.0 or meta.get("seed") != 42:
        raise ValueError("official deterministic metadata is required")
    for task_id, answer in answers.items():
        for field in ["focal_id", "target", "control", "content_scope", "policy", "plan_events"]:
            if field not in answer:
                raise ValueError(f"{task_id} missing {field}")
        if answer["control"] not in VALID_CONTROLS:
            raise ValueError(f"{task_id} has invalid control")
        if (answer.get("content_scope") or {}).get("mode") not in VALID_SCOPE_MODES:
            raise ValueError(f"{task_id} has invalid scope mode")
        if not isinstance(answer.get("plan_events"), list) or len(answer["plan_events"]) > 18:
            raise ValueError(f"{task_id} has invalid plan_events")


def write_submission_csv(payload: dict[str, Any], output_path: str | Path) -> None:
    validate_payload(payload)
    path = Path(output_path)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["submission"])
        writer.writeheader()
        writer.writerow({"submission": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))})
```

- [ ] **Step 4: Run interface tests and verify they pass**

Run:

```bash
python3 -m unittest tests.test_harness -v
```

Expected: all three tests pass.

- [ ] **Step 5: Commit skeleton implementation**

Run:

```bash
git add harness.py tests/test_harness.py
git commit -m "feat: add harness skeleton and runner"
```

## Task 3: Add Focal Resolver Tests and Trace-Based Resolution

**Files:**
- Modify: `tests/test_harness.py`
- Modify: `harness.py`

- [ ] **Step 1: Add tests for marker trace, direct object id, and selected WM history**

Append to `HarnessInterfaceTests` in `tests/test_harness.py`:

```python
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
```

- [ ] **Step 2: Run focal tests and verify they fail under first-object fallback**

Run:

```bash
python3 -m unittest tests.test_harness.HarnessInterfaceTests.test_focal_resolution_uses_marker_trace tests.test_harness.HarnessInterfaceTests.test_focal_resolution_uses_direct_object_id_record tests.test_harness.HarnessInterfaceTests.test_focal_resolution_uses_final_candidate_from_history -v
```

Expected: at least two failures showing `obj_a` selected instead of `obj_b`.

- [ ] **Step 3: Replace `choose_focal` with trace/direct/history scoring**

In `harness.py`, replace `choose_focal` with:

```python
def _walk_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_walk_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_strings(item))
    return found


def _effective_phase(view: TaskView, trace: dict[str, Any]) -> str:
    latest = str(trace.get("latest_phase") or "")
    source = str(trace.get("phase_source") or "")
    source_value = view.record_value(source) if source else None
    phase_rule = trace.get("latest_phase_rule") if isinstance(trace.get("latest_phase_rule"), dict) else {}
    if isinstance(source_value, str) and source_value in phase_rule:
        return str(phase_rule[source_value])
    return latest


def choose_focal(view: TaskView) -> dict[str, Any]:
    objects = view.objects
    if not objects:
        return {}
    by_id = view.object_by_id()
    by_ref = view.object_by_ref()

    marker_refs = view.record_value("focal_marker_refs")
    trace = view.record_value("focal_resolution_trace")
    if isinstance(marker_refs, dict) and isinstance(trace, dict):
        marker_map = marker_refs.get("marker_to_ref")
        phase_to_marker = trace.get("phase_to_marker")
        if isinstance(marker_map, dict) and isinstance(phase_to_marker, dict):
            phase = _effective_phase(view, trace)
            marker = str(phase_to_marker.get(phase) or "")
            ref = str(marker_map.get(marker) or "")
            if ref in by_ref:
                return by_ref[ref]

    for record in reversed(view.records):
        for candidate in _walk_strings(record.get("value")):
            if candidate in by_id:
                return by_id[candidate]

    history = view.history_text
    refs = re.findall(r"WM-\d+", history)
    if refs:
        positive_terms = ["최종", "확정", "현재 처리", "승인 후보", "처리 대상으로 확정", "selected", "final"]
        negative_terms = ["제외", "보류", "decoy"]
        best_ref = ""
        best_score = -10_000
        for ref in refs:
            idx = history.find(ref)
            window = history[max(0, idx - 80): idx + 120]
            score = refs.index(ref)
            score += 10 * sum(term in window for term in positive_terms)
            score -= 10 * sum(term in window for term in negative_terms)
            if score > best_score:
                best_ref = ref
                best_score = score
        if best_ref in by_ref:
            return by_ref[best_ref]

    prompt_tokens = tokens(view.prompt)
    best = objects[0]
    best_score = -1
    for obj in objects:
        attrs = obj.get("attrs") or {}
        obj_text = text_of({"type": obj.get("type"), "attrs": attrs}).lower()
        score = sum(1 for token in prompt_tokens if token in obj_text)
        if str(attrs.get("ref_code") or "") and str(attrs.get("ref_code")) in view.history_text:
            score += 2
        if score > best_score:
            best = obj
            best_score = score
    return best
```

- [ ] **Step 4: Run focal tests and full unit tests**

Run:

```bash
python3 -m unittest tests.test_harness -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit focal resolver**

Run:

```bash
git add harness.py tests/test_harness.py
git commit -m "feat: resolve focal objects from records and history"
```

## Task 4: Add Target, Control, Scope, Policy, and Plan Rule Tests

**Files:**
- Modify: `tests/test_harness.py`
- Modify: `harness.py`

- [ ] **Step 1: Add tests for local update, ask, amend, and hold decisions**

Append to `HarnessInterfaceTests`:

```python
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
```

- [ ] **Step 2: Run new decision tests and verify failures under minimal rules**

Run:

```bash
python3 -m unittest tests.test_harness.HarnessInterfaceTests.test_local_update_targets_memory_store_and_status_only tests.test_harness.HarnessInterfaceTests.test_ambiguous_target_requires_ask tests.test_harness.HarnessInterfaceTests.test_sensitive_external_share_is_redacted_amend tests.test_harness.HarnessInterfaceTests.test_precondition_invalidated_holds -v
```

Expected: failures for target/control/scope/policy mismatches.

- [ ] **Step 3: Add canonical field and signal tables**

In `harness.py`, after constants, add:

```python
SENSITIVE_FIELDS = {"raw_quote", "rrn", "name", "location", "numeric_value", "doctor_note", "card_number", "address"}
LOCAL_UPDATE_VALUES = {"local_update_boundary", "local_update", "local_update_only", "memory_write"}
ASK_RECORD_TYPES = {"ambiguous_target", "ambiguous_focal", "duration_ambiguous", "memory_conflict", "amount_changed", "merchant_verification", "routine_scope", "calendar_conflict"}
HOLD_RECORD_TYPES = {"security_alert", "safety_mode", "privacy_guard"}
PRECONDITION_RECORD_TYPES = {"target_changed_after_turn", "ops_memory_recall"}
EXTERNAL_RECORD_TYPES = {"external_share_policy", "enterprise_policy_recall", "health_share_policy"}
```

- [ ] **Step 4: Replace target/control/scope/policy/plan functions with rule-based versions**

Replace `infer_target`, `decide_control`, `build_content_scope`, `build_policy`, and `build_plan_events` in `harness.py` with:

```python
def _record_values_text(view: TaskView) -> str:
    return " ".join(text_of(record.get("value")) for record in view.records).lower()


def _is_local_update(view: TaskView) -> bool:
    values = _record_values_text(view)
    prompt = view.prompt.lower()
    return (
        "persistent_memory_write" in view.record_types
        or any(value in values for value in LOCAL_UPDATE_VALUES)
        or "내부 상태 업데이트" in prompt
        or "바깥으로 보내지 말고" in prompt
        or "local_update" in values
    )


def infer_target(view: TaskView, focal: dict[str, Any], control: str, session: dict[str, Any]) -> str:
    if _is_local_update(view):
        return "memory_store"
    if control == "ask":
        return "user"
    resolved = view.record_value("resolved_target")
    if isinstance(resolved, dict):
        for key in ("target", "route", "value", "name", "recipient", "channel"):
            if resolved.get(key):
                return str(resolved[key])
    if isinstance(resolved, str) and resolved:
        return resolved
    attrs = focal.get("attrs") or {}
    for key in ("recipient", "target", "channel", "app", "merchant", "attendee", "name"):
        if attrs.get(key):
            return str(attrs[key])
    return str(session.get("last_target") or "user")


def decide_control(view: TaskView, focal: dict[str, Any], evidence: dict[str, Any]) -> str:
    types = view.record_types
    values = _record_values_text(view)
    prompt = view.prompt.lower()
    if types & HOLD_RECORD_TYPES or "phishing" in values or "피싱" in values or "impersonation" in values:
        return "hold"
    if types & PRECONDITION_RECORD_TYPES and any(word in values or word in prompt for word in ["invalidated", "바뀐", "변경", "precondition"]):
        return "hold"
    if "consent" in types and any(word in values for word in ["revoked", "withdraw", "denied", "철회", "거부"]):
        return "hold"
    if _is_local_update(view):
        return "proceed"
    if types & ASK_RECORD_TYPES or any(word in prompt for word in ["다시 확인", "확인해", "모호", "누구에게 어떤 범위"]):
        return "ask"
    if types & EXTERNAL_RECORD_TYPES or evidence.get("requires_redaction") or contained_fields(focal) & SENSITIVE_FIELDS:
        return "amend"
    return "proceed"


def build_content_scope(view: TaskView, focal: dict[str, Any], control: str, evidence: dict[str, Any]) -> dict[str, Any]:
    contains = contained_fields(focal)
    excluded = sorted(contains & SENSITIVE_FIELDS)
    if control == "hold":
        return {"mode": "none", "allowed_fields": [], "excluded_fields": [], "requires_user_confirmation": False}
    if control == "ask":
        return {"mode": "summary", "allowed_fields": ["status"], "excluded_fields": excluded, "requires_user_confirmation": True}
    if _is_local_update(view):
        local_excluded = sorted((contains & SENSITIVE_FIELDS) | {"raw_quote", "location", "numeric_value"})
        return {"mode": "status_only", "allowed_fields": ["status"], "excluded_fields": local_excluded, "requires_user_confirmation": False}
    if control == "amend":
        return {"mode": "redacted", "allowed_fields": ["summary"], "excluded_fields": excluded or ["raw_quote"], "requires_user_confirmation": False}
    if "raw" in _record_values_text(view) and not excluded:
        return {"mode": "raw", "allowed_fields": ["raw"], "excluded_fields": [], "requires_user_confirmation": False}
    return {"mode": "summary", "allowed_fields": ["summary", "status"], "excluded_fields": excluded, "requires_user_confirmation": False}


def build_policy(view: TaskView, focal: dict[str, Any], control: str, scope: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    flags = set(evidence.get("risk_flags") or [])
    types = view.record_types
    values = _record_values_text(view)
    contains = contained_fields(focal)
    if "session_share_policy" in types:
        flags.add("strict_share_policy")
    if _is_local_update(view):
        flags.add("local_only")
    if types & EXTERNAL_RECORD_TYPES or "external" in values or "외부" in view.prompt:
        flags.add("external_share")
    if contains & SENSITIVE_FIELDS or "sensitive" in values:
        flags.add("sensitive_content")
    if "ambiguous_target" in types:
        flags.add("target_ambiguity")
    if "ambiguous_focal" in types:
        flags.add("ambiguous_focal")
    if control == "ask":
        flags.add("clarification_required")
    if control == "amend" or scope.get("mode") == "redacted":
        flags.add("minimal_disclosure")
    if types & PRECONDITION_RECORD_TYPES or "precondition" in values:
        flags.add("precondition_changed")
    if control == "hold":
        flags.add("safety")
    violations: set[str] = set()
    if control == "hold" and ("precondition" in values or types & PRECONDITION_RECORD_TYPES):
        flags.add("precondition_invalidated")
        violations.add("precondition_changed_ignored")
    return {"risk_flags": sorted(flags), "violations": sorted(violations), "requires_confirmation": control == "ask"}


def build_plan_events(focal_id: str, target: str, control: str, scope: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    if control == "hold":
        purpose = "invalidated_precondition" if "precondition_invalidated" in policy.get("risk_flags", []) else "inspect_context"
        reason = "precondition_invalidated" if "precondition_invalidated" in policy.get("risk_flags", []) else "strict_policy_block"
        return [
            {"verb": "read", "target": focal_id, "args": {"purpose": purpose}},
            {"verb": "guard", "target": focal_id, "args": {"reason": reason}},
        ]
    events = [{"verb": "read", "target": focal_id, "args": {"purpose": "inspect_context"}}]
    if control == "ask":
        reason = "target_ambiguity" if "target_ambiguity" in policy.get("risk_flags", []) else "clarification_required"
        events.append({"verb": "clarify", "target": "user", "args": {"reason": reason}})
    elif scope.get("mode") == "status_only" and target == "memory_store":
        events.append({"verb": "verify", "target": "share_boundary_update", "args": {"scope": "local_update"}})
        events.append({"verb": "update", "target": focal_id, "args": {"state": "local_status_only"}})
    elif control == "amend":
        events.append({"verb": "redact", "target": focal_id, "args": {"remove": "sensitive_fields"}})
        events.append({"verb": "dispatch", "target": target, "args": {"scope": "redacted"}})
    else:
        events.append({"verb": "dispatch", "target": target, "args": {"scope": scope.get("mode", "summary")}})
    return events[:18]
```

- [ ] **Step 5: Run full unit tests**

Run:

```bash
python3 -m unittest tests.test_harness -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit decision rules**

Run:

```bash
git add harness.py tests/test_harness.py
git commit -m "feat: add core harness decision rules"
```

## Task 5: Add CLI Scripts for Submission and Dev Evaluation

**Files:**
- Create: `generate_submission.py`
- Create: `evaluate_dev.py`
- Modify: `tests/test_harness.py`

- [ ] **Step 1: Add JSONL loader tests**

Append to `tests/test_harness.py` imports:

```python
from harness import load_jsonl
```

Append to `HarnessInterfaceTests`:

```python
    def test_load_jsonl_reads_nonempty_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.jsonl"
            path.write_text(json.dumps(make_task(), ensure_ascii=False) + "\n\n", encoding="utf-8")
            rows = load_jsonl(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "task_1")
```

- [ ] **Step 2: Add `load_jsonl` to `harness.py`**

Append to `harness.py`:

```python
def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no} is not a JSON object")
            rows.append(value)
    return rows
```

- [ ] **Step 3: Create submission CLI**

Create `generate_submission.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from harness import load_jsonl, run_harness, write_submission_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SCPC 2026 submission.csv from task JSONL.")
    parser.add_argument("--tasks", required=True, help="Path to screening_tasks.jsonl or another task JSONL file.")
    parser.add_argument("--output", default="submission.csv", help="Output CSV path.")
    parser.add_argument("--harness-name", default="scpc_rule_harness", help="Metadata harness name.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tasks = load_jsonl(args.tasks)
    payload = run_harness(tasks, harness_name=args.harness_name)
    write_submission_csv(payload, Path(args.output))
    print(f"wrote {args.output} with {len(payload['answers'])} answers")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create dev evaluator CLI**

Create `evaluate_dev.py`:

```python
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from harness import load_jsonl, run_harness


def as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, list):
        value = [value]
    return {json.dumps(item, ensure_ascii=False, sort_keys=True) if not isinstance(item, str) else item for item in value}


def f1(pred: set[str], ref: set[str]) -> float:
    if not pred and not ref:
        return 1.0
    if not pred or not ref:
        return 0.0
    hit = len(pred & ref)
    if hit == 0:
        return 0.0
    precision = hit / len(pred)
    recall = hit / len(ref)
    return 2 * precision * recall / (precision + recall)


def answer_score(pred: dict[str, Any], ref: dict[str, Any]) -> dict[str, float]:
    pred_events = pred.get("plan_events", [])
    ref_events = ref.get("expected_events", ref.get("plan_events", []))
    return {
        "focal": float(pred.get("focal_id") == ref.get("focal_id")),
        "target": float(pred.get("target") == ref.get("target")),
        "control": float(pred.get("control") == ref.get("control")),
        "scope_mode": float((pred.get("content_scope") or {}).get("mode") == (ref.get("content_scope") or {}).get("mode")),
        "allowed_fields": f1(as_set((pred.get("content_scope") or {}).get("allowed_fields")), as_set((ref.get("content_scope") or {}).get("allowed_fields"))),
        "excluded_fields": f1(as_set((pred.get("content_scope") or {}).get("excluded_fields")), as_set((ref.get("content_scope") or {}).get("excluded_fields"))),
        "risk_flags": f1(as_set((pred.get("policy") or {}).get("risk_flags")), as_set((ref.get("policy") or {}).get("risk_flags"))),
        "violations": f1(as_set((pred.get("policy") or {}).get("violations")), as_set((ref.get("policy") or {}).get("violations"))),
        "plan_verbs": f1(as_set([event.get("verb") for event in pred_events]), as_set([event.get("verb") for event in ref_events])),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate harness on public dev references.")
    parser.add_argument("--tasks", required=True, help="Path to dev_tasks.jsonl.")
    parser.add_argument("--answers", required=True, help="Path to dev_answers.json.")
    parser.add_argument("--show", type=int, default=8, help="Number of mismatches to print.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tasks = load_jsonl(args.tasks)
    refs = json.loads(Path(args.answers).read_text(encoding="utf-8"))["answers"]
    payload = run_harness(tasks, harness_name="scpc_rule_harness_dev_eval")
    totals: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    mismatches: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, float]]] = []
    for task in tasks:
        task_id = str(task["id"])
        pred = payload["answers"][task_id]
        ref = refs[task_id]
        scores = answer_score(pred, ref)
        for key, value in scores.items():
            totals[key] += value
            counts[key] += 1
        if pred.get("control") != ref.get("control") or pred.get("focal_id") != ref.get("focal_id"):
            mismatches.append((task_id, pred, ref, scores))
    for key in sorted(totals):
        print(f"{key}: {totals[key] / counts[key]:.3f}")
    print(f"mismatches: {len(mismatches)} / {len(tasks)}")
    for task_id, pred, ref, scores in mismatches[: args.show]:
        print(json.dumps({"task_id": task_id, "pred": pred, "ref": ref, "scores": scores}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m unittest tests.test_harness -v
```

Expected: all tests pass.

- [ ] **Step 6: Run dev evaluator on public data**

Run:

```bash
python3 evaluate_dev.py --tasks "/Users/hanjeonghyun/Downloads/data 3/dev_tasks.jsonl" --answers "/Users/hanjeonghyun/Downloads/data 3/dev_answers.json" --show 5
```

Expected: field scores print and the script exits with code 0.

- [ ] **Step 7: Commit CLI scripts**

Run:

```bash
git add harness.py generate_submission.py evaluate_dev.py tests/test_harness.py
git commit -m "feat: add submission and dev evaluation CLIs"
```

## Task 6: Calibrate Rules Against Public Dev Without Task-ID Hardcoding

**Files:**
- Modify: `harness.py`
- Modify: `tests/test_harness.py`

- [ ] **Step 1: Run current dev diagnostics and capture weak fields**

Run:

```bash
python3 evaluate_dev.py --tasks "/Users/hanjeonghyun/Downloads/data 3/dev_tasks.jsonl" --answers "/Users/hanjeonghyun/Downloads/data 3/dev_answers.json" --show 12
```

Expected: field scores and representative mismatches. Use the mismatch output to select general record-level rules only.

- [ ] **Step 2: Add anti-hardcoding test**

Append to `HarnessInterfaceTests`:

```python
    def test_harness_source_does_not_hardcode_task_or_session_ids(self):
        source = Path("harness.py").read_text(encoding="utf-8")
        forbidden_patterns = ["final_dev_", "final_screening_", "task_id ==", "session_id ==", "dev_answers.json"]
        for pattern in forbidden_patterns:
            self.assertNotIn(pattern, source)
```

- [ ] **Step 3: Improve general policy rules from diagnostics**

Modify `harness.py` using only general record/value conditions:

```python
def _has_value(view: TaskView, *needles: str) -> bool:
    values = _record_values_text(view) + " " + view.prompt.lower() + " " + view.history_text.lower()
    return any(needle.lower() in values for needle in needles)
```

Then extend `decide_control` before the final `return "proceed"`:

```python
    if _has_value(view, "dispatch_blocked_until_binding", "authority_incomplete", "raw_sensitive_forbidden", "privacy_rule_violation"):
        return "hold"
    if _has_value(view, "redacted_summary_only", "summary_only", "minimal_disclosure", "식별 가능한 세부값을 제외"):
        return "amend"
    if _has_value(view, "confirmation_required", "route_resolution_required", "target_conflict"):
        return "ask"
```

Extend `build_policy` before violations:

```python
    if _has_value(view, "dispatch_blocked_until_binding", "authority_incomplete"):
        flags.add("target_ambiguity")
    if _has_value(view, "redacted_summary_only", "summary_only", "minimal_disclosure", "식별 가능한 세부값을 제외"):
        flags.add("minimal_disclosure")
    if _has_value(view, "dispatch_blocked_until_binding", "authority_incomplete", "raw_sensitive_forbidden", "privacy_rule_violation"):
        flags.add("safety")
```

- [ ] **Step 4: Run anti-hardcoding and full tests**

Run:

```bash
python3 -m unittest tests.test_harness -v
```

Expected: all tests pass, including the source scan.

- [ ] **Step 5: Rerun dev evaluator and compare scores**

Run:

```bash
python3 evaluate_dev.py --tasks "/Users/hanjeonghyun/Downloads/data 3/dev_tasks.jsonl" --answers "/Users/hanjeonghyun/Downloads/data 3/dev_answers.json" --show 8
```

Expected: no crash and at least focal/control/scope diagnostics are available. If a rule reduces a field score obviously, revert only that general condition with `apply_patch`.

- [ ] **Step 6: Commit calibrated general rules**

Run:

```bash
git add harness.py tests/test_harness.py
git commit -m "feat: calibrate harness rules from dev diagnostics"
```

## Task 7: Generate and Validate Screening Submission

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Generate `submission.csv` from screening tasks**

Run:

```bash
python3 generate_submission.py --tasks "/Users/hanjeonghyun/Downloads/data 3/screening_tasks.jsonl" --output submission.csv
```

Expected: `wrote submission.csv with 700 answers`.

- [ ] **Step 2: Validate CSV round trip and answer count**

Run:

```bash
python3 -c 'import csv,json; rows=list(csv.DictReader(open("submission.csv", encoding="utf-8-sig"))); assert list(rows[0])==["submission"]; payload=json.loads(rows[0]["submission"]); assert payload["schema"]=="scpc.final.answer.v1"; assert len(payload["answers"])==700; assert payload["meta"]["uses_external_api"] is False; print("valid submission", len(payload["answers"]))'
```

Expected: `valid submission 700`.

- [ ] **Step 3: Add README execution instructions**

Replace `README.md` with:

```markdown
# scpc2026-ai-harness

Deterministic Python harness for the SCPC 2026 AI Agent Harness task.

## Rules

- Uses Python standard library only.
- Does not call external APIs, remote models, or network services.
- Exposes `FinalHarness.answer_task(task, session)` for organizer verification.
- Uses `FixedSLMClient.summarize_task()` only as local evidence; final answer fields are produced by harness rules.
- Does not hardcode task ids, session ids, screening rows, or answer maps.

## Generate Submission

```bash
python3 generate_submission.py \
  --tasks "/Users/hanjeonghyun/Downloads/data 3/screening_tasks.jsonl" \
  --output submission.csv
```

The output file is UTF-8 CSV with one column, `submission`, and one data row containing the full answer JSON.

## Dev Diagnostics

```bash
python3 evaluate_dev.py \
  --tasks "/Users/hanjeonghyun/Downloads/data 3/dev_tasks.jsonl" \
  --answers "/Users/hanjeonghyun/Downloads/data 3/dev_answers.json" \
  --show 8
```

## Tests

```bash
python3 -m unittest tests.test_harness -v
```

## Submission Metadata

The generated JSON uses:

- `fixed_slm_policy`: `local_fixed_slm_only`
- `model_id`: `scpc-final-fixed-slm-local-facade`
- `temperature`: `0.0`
- `seed`: `42`
```

- [ ] **Step 4: Run final tests**

Run:

```bash
python3 -m unittest tests.test_harness -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit README and generated submission if desired**

Run:

```bash
git add README.md submission.csv
git commit -m "docs: add harness usage and submission artifact"
```

If `submission.csv` should remain uncommitted, run this instead:

```bash
git add README.md
git commit -m "docs: add harness usage"
```

## Task 8: Final Verification

**Files:**
- No file changes expected unless verification reveals a defect.

- [ ] **Step 1: Check git status**

Run:

```bash
git status --short
```

Expected: either clean, or only `submission.csv` modified/untracked if not committed.

- [ ] **Step 2: Run full verification suite**

Run:

```bash
python3 -m unittest tests.test_harness -v
python3 evaluate_dev.py --tasks "/Users/hanjeonghyun/Downloads/data 3/dev_tasks.jsonl" --answers "/Users/hanjeonghyun/Downloads/data 3/dev_answers.json" --show 3
python3 generate_submission.py --tasks "/Users/hanjeonghyun/Downloads/data 3/screening_tasks.jsonl" --output submission.csv
python3 -c 'import csv,json; rows=list(csv.DictReader(open("submission.csv", encoding="utf-8-sig"))); payload=json.loads(rows[0]["submission"]); assert len(payload["answers"])==700; print(payload["schema"], len(payload["answers"]))'
```

Expected: tests pass, evaluator prints diagnostics, submission generator writes 700 answers, final command prints `scpc.final.answer.v1 700`.

- [ ] **Step 3: Scan for forbidden hardcoding and network imports**

Run:

```bash
python3 -c 'from pathlib import Path; text="\n".join(p.read_text(encoding="utf-8") for p in [Path("harness.py"),Path("generate_submission.py"),Path("evaluate_dev.py")]); forbidden=["final_dev_","final_screening_","requests","urllib","httpx","openai","anthropic","socket"]; hits=[x for x in forbidden if x in text]; assert not hits, hits; print("no forbidden patterns")'
```

Expected: `no forbidden patterns`.

- [ ] **Step 4: Commit any verification fixes**

If a defect was fixed during final verification, run:

```bash
git add harness.py generate_submission.py evaluate_dev.py tests/test_harness.py README.md submission.csv
git commit -m "fix: finalize harness verification"
```

If no file changed, skip this commit.

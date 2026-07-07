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
SENSITIVE_FIELDS = {"raw_quote", "rrn", "name", "location", "numeric_value", "doctor_note", "card_number", "address"}
LOCAL_UPDATE_VALUES = {"local_update_boundary", "local_update", "local_update_only", "memory_write"}
ASK_RECORD_TYPES = {"ambiguous_target", "ambiguous_focal", "duration_ambiguous", "memory_conflict", "amount_changed", "merchant_verification", "routine_scope", "calendar_conflict"}
HOLD_RECORD_TYPES = {"security_alert", "safety_mode", "privacy_guard"}
PRECONDITION_RECORD_TYPES = {"target_changed_after_turn", "ops_memory_recall"}
EXTERNAL_RECORD_TYPES = {"external_share_policy", "enterprise_policy_recall", "health_share_policy"}


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
        for index, ref in enumerate(refs):
            ref_index = history.find(ref)
            left = max(history.rfind(".", 0, ref_index), history.rfind("。", 0, ref_index), history.rfind("\n", 0, ref_index))
            right_candidates = [pos for pos in [history.find(".", ref_index), history.find("。", ref_index), history.find("\n", ref_index)] if pos != -1]
            right = min(right_candidates) if right_candidates else len(history)
            window = history[left + 1:right]
            score = index
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


def contained_fields(focal: dict[str, Any]) -> set[str]:
    contains = (focal.get("attrs") or {}).get("contains")
    return {str(item) for item in contains} if isinstance(contains, list) else set()


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

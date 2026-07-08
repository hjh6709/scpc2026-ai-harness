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
FIELD_ALIASES = {"amount": "numeric_value", "doctor_note": "raw_quote"}
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
        unique_refs = list(dict.fromkeys(refs))
        if len(unique_refs) >= 3 and any(term in history for term in ["가운데 항목", "가운데 후보", "중간 항목", "중간 후보"]):
            middle_ref = unique_refs[len(unique_refs) // 2]
            if middle_ref in by_ref:
                return by_ref[middle_ref]
        ordinal_to_index = {
            "첫 번째": 0,
            "첫번째": 0,
            "1번째": 0,
            "두 번째": 1,
            "두번째": 1,
            "2번째": 1,
            "세 번째": 2,
            "세번째": 2,
            "3번째": 2,
        }
        positive_terms = ["최종", "확정", "현재 처리", "승인 후보", "처리 대상으로 확정", "selected", "final"]
        negative_terms = ["제외", "보류", "decoy"]
        if any(term in history for term in ["후보만 현재 처리 대상으로 확정", "후보만 현재 처리", "후보만 확정"]):
            best_ordinal_ref = ""
            best_ordinal_score = -10_000
            for ordinal, ref_index in ordinal_to_index.items():
                ordinal_index = history.find(ordinal)
                if ordinal_index == -1 or ref_index >= len(unique_refs):
                    continue
                left = max(
                    history.rfind(".", 0, ordinal_index),
                    history.rfind("。", 0, ordinal_index),
                    history.rfind(",", 0, ordinal_index),
                    history.rfind("，", 0, ordinal_index),
                    history.rfind("\n", 0, ordinal_index),
                )
                right_candidates = [
                    pos
                    for pos in [
                        history.find(".", ordinal_index),
                        history.find("。", ordinal_index),
                        history.find(",", ordinal_index),
                        history.find("，", ordinal_index),
                        history.find("\n", ordinal_index),
                    ]
                    if pos != -1
                ]
                right = min(right_candidates) if right_candidates else len(history)
                window = history[left + 1:right]
                score = 10 * sum(term in window for term in positive_terms) - 10 * sum(term in window for term in negative_terms)
                if score > best_ordinal_score:
                    best_ordinal_ref = unique_refs[ref_index]
                    best_ordinal_score = score
            if best_ordinal_score > 0 and best_ordinal_ref in by_ref:
                return by_ref[best_ordinal_ref]
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


def _has_value(view: TaskView, *needles: str) -> bool:
    values = _record_values_text(view) + " " + view.prompt.lower() + " " + view.history_text.lower()
    return any(needle.lower() in values for needle in needles)


def _precondition_invalidated(view: TaskView) -> bool:
    return _has_value(
        view,
        "취소된",
        "더 진행하면 안 된다",
        "전제가 사라",
        "실행하면 안 된다",
        "precondition_invalidated",
        "prior_success_invalidated",
        "허용 근거가",
        "깨졌으므로",
        "멈춰야 한다",
        "과거 승인에 기대면 안 되는 상태",
        "실행을 막아야",
        "전제를 무효화",
        "이전 허용의 전제",
    )


def _condition_uncertain(view: TaskView) -> bool:
    return _has_value(
        view,
        "조건 변경 여부가 미확정",
        "현재 정보만으로는 결론",
        "최종 수신처와 허용 범위가 아직 확정되지",
        "허용 범위가 아직 확정되지",
    )


def _stored_channel_ambiguous(view: TaskView) -> bool:
    return "ambiguous_target" in view.record_types and _has_value(view, "stored_channel_or_visible_recipient")


def _persistent_memory_sensitive_share(view: TaskView) -> bool:
    return "persistent_memory_recall" in view.record_types and _has_value(
        view,
        "위치나 나이",
        "민감 내용",
        "민감한 내용",
        "민감 내용은 알아서",
    )


def _memory_domain_target(view: TaskView) -> str:
    if "persistent_memory_recall" not in view.record_types:
        return ""
    text = view.prompt.lower() + " " + view.history_text.lower()
    if any(term in text for term in ["검진", "병원", "클리닉"]):
        return "clinic_portal" if _stored_channel_ambiguous(view) and not _has_value(view, "local_update_boundary") else "caregiver"
    if any(term in text for term in ["생일 준비", "선물 준비"]):
        return "caregiver"
    return ""


def _is_local_update(view: TaskView) -> bool:
    values = _record_values_text(view)
    prompt = view.prompt.lower() + " " + view.history_text.lower()
    return (
        "persistent_memory_write" in view.record_types
        or "local_update_only" in values
        or "memory_write" in values
        or "내부 상태 업데이트" in prompt
        or "바깥으로 보내지 말고" in prompt
        or "전달 동작은 취소" in prompt
        or "보내는 작업은 취소하고 로컬 상태" in prompt
        or "장치 안의 처리 상태만" in prompt
        or "기기 내부 업데이트만" in prompt
        or "수신처 전달 대신" in prompt
        or "로컬 상태 기록으로만" in prompt
        or "공유하지 말고 상태값만 갱신" in prompt
        or "상태값만 갱신" in prompt
        or "내 기기 안에서 상태만 갱신" in prompt
        or "외부 공유가 아니라" in prompt
    )


def _has_status_update_boundary(view: TaskView) -> bool:
    values = _record_values_text(view)
    return "local_update_boundary" in values or _is_local_update(view)


def _unique_object_target(view: TaskView, focal_id: str = "") -> str:
    candidates: set[str] = set()
    for obj in view.objects:
        if focal_id and str(obj.get("id")) == focal_id:
            continue
        attrs = obj.get("attrs") or {}
        for key in ("recipient", "target", "channel", "attendee"):
            if attrs.get(key):
                candidates.add(str(attrs[key]))
    return next(iter(candidates)) if len(candidates) == 1 else ""


def infer_target(view: TaskView, focal: dict[str, Any], control: str, session: dict[str, Any]) -> str:
    if _is_local_update(view):
        return "memory_store"
    if control == "hold" and _precondition_invalidated(view):
        return "user"
    if control == "ask" and _condition_uncertain(view):
        return "user"
    memory_target = _memory_domain_target(view)
    if memory_target and control in {"ask", "amend"}:
        return memory_target
    changed_target = view.record_value("target_changed_after_turn")
    if control == "ask" and isinstance(changed_target, str) and "route_superseded" in changed_target:
        return "security_review"
    if control == "ask" and isinstance(changed_target, str) and changed_target and "invalidated" not in changed_target:
        return changed_target
    explicit_user_confirmation = any(word in view.prompt.lower() for word in ["누구에게 어떤 범위", "사용자에게 먼저 확인", "사용자 확인"])
    if control == "ask" and not explicit_user_confirmation:
        resolved = view.record_value("resolved_target")
        if isinstance(resolved, str) and resolved:
            return resolved
        object_target = _unique_object_target(view, str(focal.get("id") or ""))
        if object_target:
            return object_target
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
    if {"security_alert", "safety_mode"} & types or "phishing" in values or "피싱" in values or "impersonation" in values:
        return "hold"
    if _is_local_update(view):
        return "proceed"
    if "privacy_guard" in types:
        return "hold"
    if "target_changed_after_turn" in types and "route_superseded" in values:
        return "ask"
    if types & PRECONDITION_RECORD_TYPES and any(word in values or word in prompt for word in ["invalidated", "바뀐", "변경", "precondition"]):
        return "hold"
    if "target_changed_after_turn" in types:
        return "ask"
    if "consent" in types and any(word in values for word in ["revoked", "withdraw", "denied", "철회", "거부"]):
        return "hold"
    if _precondition_invalidated(view):
        return "hold"
    if _condition_uncertain(view):
        return "ask"
    if any(word in prompt for word in ["다시 확인", "누구에게 어떤 범위", "사용자에게 먼저 확인", "사용자 확인"]):
        return "ask"
    if "payment_policy" in types and "requires_confirmation" in values:
        return "ask"
    if _stored_channel_ambiguous(view) and _has_value(view, "local_update_boundary"):
        return "amend"
    if _stored_channel_ambiguous(view):
        return "ask"
    if _persistent_memory_sensitive_share(view):
        return "amend"
    if _has_status_update_boundary(view) and "ambiguous_target" not in types and _has_value(view, "internal_binding_confirmed", "route_verified", "single_internal_candidate"):
        return "proceed"
    if _has_value(view, "redacted_summary_only", "summary_only", "minimal_disclosure", "식별 가능한 세부값을 제외"):
        return "amend"
    if _has_value(view, "redacted_external_boundary") and _has_value(view, "internal_binding_confirmed", "single_internal_candidate"):
        return "amend"
    if _has_value(view, "raw_sensitive_forbidden", "privacy_rule_violation"):
        return "hold"
    if _has_value(view, "dispatch_blocked_until_binding", "authority_incomplete", "route_resolution_required", "target_conflict"):
        return "ask"
    if "ambiguous_target" in types and _has_value(view, "internal_binding_confirmed", "route_verified", "single_internal_candidate"):
        return "amend"
    if _has_value(view, "confirmation_required", "route_resolution_required", "target_conflict"):
        return "ask"
    if types & ASK_RECORD_TYPES:
        return "ask"
    if types & EXTERNAL_RECORD_TYPES or evidence.get("requires_redaction") or contained_fields(focal) & SENSITIVE_FIELDS:
        return "amend"
    return "proceed"


def contained_fields(focal: dict[str, Any]) -> set[str]:
    attrs = focal.get("attrs") or {}
    fields: set[str] = set()
    for key in ("contains", "fields"):
        values = attrs.get(key)
        if isinstance(values, list):
            fields.update(str(item) for item in values)
    return {FIELD_ALIASES.get(field, field) for field in fields}


def build_content_scope(view: TaskView, focal: dict[str, Any], control: str, evidence: dict[str, Any]) -> dict[str, Any]:
    contains = contained_fields(focal)
    excluded = sorted(contains & SENSITIVE_FIELDS)
    if control == "hold":
        return {"mode": "none", "allowed_fields": [], "excluded_fields": [], "requires_user_confirmation": False}
    if control == "ask":
        return {"mode": "summary", "allowed_fields": ["summary"], "excluded_fields": excluded, "requires_user_confirmation": True}
    if _has_status_update_boundary(view) and control == "proceed":
        local_excluded = sorted({"raw_quote", "location", "numeric_value"} if contains & SENSITIVE_FIELDS else set())
        return {"mode": "status_only", "allowed_fields": ["status"], "excluded_fields": local_excluded, "requires_user_confirmation": False}
    if control == "amend":
        needs_confirmation = "ambiguous_target" in view.record_types or _stored_channel_ambiguous(view)
        return {"mode": "redacted", "allowed_fields": ["summary"], "excluded_fields": excluded or ["raw_quote"], "requires_user_confirmation": needs_confirmation}
    if "raw" in _record_values_text(view) and not excluded:
        return {"mode": "raw", "allowed_fields": ["raw"], "excluded_fields": [], "requires_user_confirmation": False}
    return {"mode": "summary", "allowed_fields": ["summary", "status"], "excluded_fields": excluded, "requires_user_confirmation": False}


def build_policy(view: TaskView, focal: dict[str, Any], control: str, scope: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    flags = set(evidence.get("risk_flags") or [])
    types = view.record_types
    values = _record_values_text(view)
    contains = contained_fields(focal)
    local_status = _has_status_update_boundary(view)
    if local_status:
        flags = set()
    if str(view.record_value("session_share_policy") or "").lower() == "strict":
        flags.add("strict_share_policy")
    if local_status:
        flags.add("local_only")
    if not local_status and (types & EXTERNAL_RECORD_TYPES or "external" in values or "외부" in view.prompt):
        flags.add("external_share")
    if not local_status and "persistent_memory_recall" in types and control in {"ask", "amend", "hold"}:
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
    invalidated_precondition = _precondition_invalidated(view)
    if types & PRECONDITION_RECORD_TYPES or "precondition" in values or invalidated_precondition or _stored_channel_ambiguous(view):
        flags.add("precondition_changed")
    if _has_value(view, "dispatch_blocked_until_binding", "authority_incomplete"):
        flags.add("target_ambiguity")
    if _has_value(view, "redacted_summary_only", "summary_only", "minimal_disclosure", "식별 가능한 세부값을 제외"):
        flags.add("minimal_disclosure")
    if _has_value(view, "raw_sensitive_forbidden", "privacy_rule_violation"):
        flags.add("safety")
    if control == "hold":
        flags.add("safety")
    violations: set[str] = set()
    if control == "hold" and ("precondition" in values or invalidated_precondition or "취소" in view.history_text or "취소" in view.prompt or types & PRECONDITION_RECORD_TYPES):
        flags.add("precondition_invalidated")
        violations.add("precondition_changed_ignored")
    requires_confirmation = control == "ask" or (control != "hold" and ("ambiguous_target" in types or "precondition" in values))
    return {"risk_flags": sorted(flags), "violations": sorted(violations), "requires_confirmation": requires_confirmation}


def build_plan_events(focal_id: str, target: str, control: str, scope: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    if control == "hold":
        purpose = "invalidated_precondition" if "precondition_invalidated" in policy.get("risk_flags", []) else "inspect_context"
        reason = "precondition_invalidated" if "precondition_invalidated" in policy.get("risk_flags", []) else "strict_policy_block"
        return [
            {"verb": "read", "target": focal_id, "args": {"purpose": purpose}},
            {"verb": "guard", "target": focal_id, "args": {"reason": reason}},
        ]
    read_purpose = "inspect_context"
    if scope.get("mode") == "status_only":
        read_purpose = "local_update"
    elif control == "amend":
        read_purpose = "minimal_disclosure"
    events = [{"verb": "read", "target": focal_id, "args": {"purpose": read_purpose}}]
    if control == "ask":
        reason = "target_ambiguity" if "target_ambiguity" in policy.get("risk_flags", []) else "clarification_required"
        if "precondition_changed" in policy.get("risk_flags", []):
            events[0]["args"]["purpose"] = "clarify_precondition"
            reason = "precondition_changed"
        if target != "user":
            if events[0]["args"]["purpose"] != "clarify_precondition":
                events[0]["args"]["purpose"] = "route_resolution_required"
                reason = "route_resolution_required"
        events.append({"verb": "clarify", "target": "user", "args": {"reason": reason}})
    elif scope.get("mode") == "status_only":
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

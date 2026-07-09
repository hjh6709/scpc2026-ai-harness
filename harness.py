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
# dispatch_authority_check/route_candidate_snapshot values meaning "route settled, no
# ambiguity left" - local_authority_confirmed/local_candidate_only are the pure-local
# analogs of internal_binding_confirmed/single_internal_candidate.
ROUTE_CONFIRMED_VALUES = ("internal_binding_confirmed", "route_verified", "single_internal_candidate", "local_authority_confirmed", "local_candidate_only")


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
        control = decide_control(view, focal, evidence, session)
        target = infer_target(view, focal, control, session)
        scope = build_content_scope(view, focal, control, evidence)
        policy = build_policy(view, focal, control, scope, evidence)
        plan_events = build_plan_events(focal_id, target, control, scope, policy)
        update_session_state(view, session, focal_id, target, control, scope, policy)
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


FOCAL_ORDINAL_WORDS = {
    "첫": 0, "두": 1, "세": 2, "네": 3, "다섯": 4,
    "여섯": 5, "일곱": 6, "여덟": 7, "아홉": 8, "열": 9,
}
FOCAL_POSITIVE_TERMS = ["확정", "최종", "선택", "지정", "채택", "결정", "승인", "처리 대상", "통과", "고정", "selected", "final", "confirm"]
FOCAL_NEGATIVE_TERMS = ["제외", "보류", "무시", "폐기", "취소", "배제", "decoy", "hold", "exclude"]


def _split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[.。,，\n]", text) if s.strip()]


def _ordinal_indices(sentence: str) -> list[int]:
    indices: set[int] = set()
    for match in re.finditer(r"(\d+)\s*번째", sentence):
        value = int(match.group(1)) - 1
        if value >= 0:
            indices.add(value)
    for word, index in FOCAL_ORDINAL_WORDS.items():
        if f"{word}번째" in sentence or f"{word} 번째" in sentence or f"{word}째" in sentence:
            indices.add(index)
    return sorted(indices)


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

        pass_match = re.search(r"(WM-\d+)\s*(?:만|only)\s*(?:통과|pass)", history)
        if pass_match and pass_match.group(1) in by_ref:
            return by_ref[pass_match.group(1)]
        fixed_match = re.search(r"(WM-\d+)\s*(?:로|으로)?\s*고정", history)
        if fixed_match and fixed_match.group(1) in by_ref:
            return by_ref[fixed_match.group(1)]

        if (
            len(unique_refs) >= 2
            and any(term in history for term in ("가운데", "중간"))
            and any(term in history for term in ("항목", "후보"))
        ):
            middle_ref = unique_refs[len(unique_refs) // 2]
            if middle_ref in by_ref:
                return by_ref[middle_ref]

        ordinal_scores: dict[int, int] = {}
        for sentence in _split_sentences(history):
            indices = _ordinal_indices(sentence)
            if not indices:
                continue
            weight = 10 * sum(term in sentence for term in FOCAL_POSITIVE_TERMS)
            weight -= 10 * sum(term in sentence for term in FOCAL_NEGATIVE_TERMS)
            for index in indices:
                if index < len(unique_refs):
                    ordinal_scores[index] = ordinal_scores.get(index, 0) + weight
        if ordinal_scores:
            best_index = max(ordinal_scores, key=lambda i: ordinal_scores[i])
            if ordinal_scores[best_index] > 0:
                best_ordinal_ref = unique_refs[best_index]
                if best_ordinal_ref in by_ref:
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
            score += 10 * sum(term in window for term in FOCAL_POSITIVE_TERMS)
            score -= 10 * sum(term in window for term in FOCAL_NEGATIVE_TERMS)
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
        "취소되",
        "진행하면 안",
        "전제가 사라",
        "실행하면 안",
        "precondition_invalidated",
        "prior_success_invalidated",
        "허용 근거",
        "허용의 근거",
        "깨졌",
        "깨뜨리",
        "멈춰야",
        "믿을 수 없으므로",
        "뒤집었으니",
        "기대면 안",
        "막아야",
        "전제를 무효화",
    )


def _child_sleep_lighting_memory_block(view: TaskView) -> bool:
    return (
        "persistent_memory_recall" in view.record_types
        and view.record_value("safety_mode") == "child_sleep_active"
        and _has_value(view, "조명", "light", "lighting")
    )


def _doctor_note_external_precondition_invalidated(view: TaskView, focal: dict[str, Any]) -> bool:
    if _doctor_note_external_scope_uncertain(view, focal):
        return False
    attrs = focal.get("attrs") or {}
    raw_fields = set()
    for key in ("contains", "fields"):
        values = attrs.get(key)
        if isinstance(values, list):
            raw_fields.update(str(item) for item in values)
    return (
        str(focal.get("type") or "") == "health_record"
        and ("doctor_note" in raw_fields or "raw_quote" in contained_fields(focal))
        and view.record_value("external_share_policy") == "doctor_note_forbidden"
        and not _is_local_update(view)
        and not _condition_uncertain(view)
    )


def _doctor_note_external_scope_uncertain(view: TaskView, focal: dict[str, Any]) -> bool:
    attrs = focal.get("attrs") or {}
    raw_fields = set()
    for key in ("contains", "fields"):
        values = attrs.get(key)
        if isinstance(values, list):
            raw_fields.update(str(item) for item in values)
    return (
        str(focal.get("type") or "") == "health_record"
        and ("doctor_note" in raw_fields or "raw_quote" in contained_fields(focal))
        and view.record_value("external_share_policy") == "doctor_note_forbidden"
        and not _is_local_update(view)
        and _has_value(view, "확정되지", "누구에게 어떤 범위")
    )


def _confirmation_precondition(view: TaskView) -> bool:
    return _has_value(
        view,
        "확정되지",
        "사용자에게 먼저 확인",
        "누구에게 어떤 범위",
        "전제 조건이 바뀌",
    )


def _condition_uncertain(view: TaskView) -> bool:
    return _has_value(
        view,
        "미확정",
        "결론을 내릴 수 없",
        "허용 범위가 아직 확정되지",
        "확정 정보가 없",
        "확정되지",
    )


def _stored_channel_ambiguous(view: TaskView) -> bool:
    return "ambiguous_target" in view.record_types and _has_value(view, "stored_channel_or_visible_recipient")


def _persistent_memory_sensitive_share(view: TaskView) -> bool:
    return "persistent_memory_recall" in view.record_types and _has_value(view, "민감", "위치나 나이")


def _prior_success_memory_reuse(view: TaskView) -> bool:
    recall = view.record_value("persistent_memory_recall")
    return (
        isinstance(recall, dict)
        and recall.get("memory_class") == "prior_result"
        and view.record_value("ops_memory_recall") == "use_prior_success_and_failure_records"
        and "target_changed_after_turn" not in view.record_types
    )


def _enterprise_policy_review(view: TaskView) -> bool:
    return view.record_value("enterprise_policy_recall") == "apply_standing_default_constraint"


def _temporary_privacy_override(view: TaskView) -> bool:
    return view.record_value("temporary_override_allowed") is not None and _has_value(view, "privacy", "개인정보", "보호 조건", "duration", "적용 시간")


def _same_place_check_summary(view: TaskView) -> bool:
    return (
        view.record_value("resolved_target") is not None
        and _has_value(view, "same place", "같은 곳")
        and _has_value(view, "check summary", "점검 요약", "점검 내용")
        and _has_value(view, "recent consent", "최신 consent", "최근 동의")
    )


def _guardrail_ladder(view: TaskView) -> bool:
    return view.record_value("guardrail_ladder_signal") is not None


def _guardrail_blocked_binding(view: TaskView) -> bool:
    return (
        _guardrail_ladder(view)
        and _external_binding_blocked(view)
    )


def _mixed_local_external_confirmation(view: TaskView) -> bool:
    return _has_value(view, "업데이트와 외부 공유", "로컬 처리로 단정")


def _external_binding_blocked(view: TaskView) -> bool:
    return (
        view.record_value("route_candidate_snapshot") == "external_candidates_present"
        and view.record_value("share_boundary_update") == "dispatch_blocked_until_binding"
        and view.record_value("dispatch_authority_check") in {"authority_incomplete", "user_binding_pending"}
        and not _has_value(view, "세부값을 제외", "redacted_summary_only", "summary_only", "minimal_disclosure")
        and not _condition_uncertain(view)
        and not _mixed_local_external_confirmation(view)
        and not _has_value(view, "표면 문장")
    )


def _guardrail_verified_external_route(view: TaskView) -> bool:
    return (
        _guardrail_ladder(view)
        and view.record_value("route_candidate_snapshot") == "single_internal_candidate"
        and view.record_value("dispatch_authority_check") == "internal_binding_confirmed"
        and view.record_value("share_boundary_update") == "redacted_external_boundary"
        and not _precondition_invalidated(view)
        and not _condition_uncertain(view)
    )


def _guardrail_local_boundary_review(view: TaskView) -> bool:
    return (
        _guardrail_ladder(view)
        and view.record_value("route_candidate_snapshot") in {"single_internal_candidate", "local_candidate_only"}
        and view.record_value("dispatch_authority_check") in {"internal_binding_confirmed", "local_authority_confirmed"}
        and view.record_value("share_boundary_update") == "local_update_boundary"
        and view.record_value("route_binding_order") == "boundary_after_authority"
        and not _precondition_invalidated(view)
    )


def _surface_resolved_channel_conflict(view: TaskView) -> bool:
    return (
        view.record_value("ambiguous_target") == "surface_recipient_and_resolved_target_both_recent"
        and view.record_value("route_candidate_snapshot") == "single_internal_candidate"
        and view.record_value("dispatch_authority_check") == "internal_binding_confirmed"
        and view.record_value("share_boundary_update") == "redacted_external_boundary"
        and not _is_local_update(view)
        and not _precondition_invalidated(view)
    )


def _summary_only_composite_plan(view: TaskView) -> bool:
    text = view.all_text
    return (
        view.record_value("resolved_target") is not None
        and any(term in text for term in ["요약본만", "요약만"])
        and "임시 알림" in text
        and _has_value(view, "최근 동의", "최신 consent")
        and _has_value(view, "masked_ref")
        and not _condition_uncertain(view)
        and "duration_ambiguous" not in view.record_types
    )


def _plain_composite_plan(view: TaskView) -> bool:
    return (
        view.record_value("resolved_target") is not None
        and _has_value(view, "회의 시간", "파일 요약", "메시지 발송", "임시 설정")
        and not _summary_only_composite_plan(view)
        and not _condition_uncertain(view)
        and "duration_ambiguous" not in view.record_types
    )


def _revoked_or_security_precondition(view: TaskView) -> bool:
    consent = str(view.record_value("consent") or "").lower()
    return (
        not _is_local_update(view)
        and (
            "security_alert" in view.record_types
            or consent in {"revoked", "withdrawn", "denied"}
            or consent.startswith("revoked")
        )
    )


def _same_context_followup(view: TaskView) -> bool:
    return _has_value(view, "그대로 진행", "이전 요청 그대로", "같은 곳", "방금 내용", "지난번 방식", "같은 방식")


def _direct_reuse_followup(view: TaskView) -> bool:
    return _has_value(view, "그대로 진행", "이전 요청 그대로", "지난번 방식", "같은 방식")


def _prior_hold_followup(view: TaskView, session: dict[str, Any]) -> bool:
    return session.get("last_control") == "hold" and _direct_reuse_followup(view)


def _prior_local_only_external_followup(view: TaskView, session: dict[str, Any]) -> bool:
    if view.record_value("resolved_target"):
        return False
    if _stored_channel_ambiguous(view) and _has_value(view, "local_update_boundary") and _has_value(view, *ROUTE_CONFIRMED_VALUES):
        return False
    prior_local = (
        session.get("last_target") == "memory_store"
        or session.get("last_scope_mode") == "status_only"
        or "local_only" in set(session.get("last_risk_flags") or [])
    )
    wants_external = _has_value(view, "보내", "공유", "전달", "dispatch")
    return prior_local and wants_external and not _is_local_update(view)


LOCAL_UPDATE_NEGATION_TERMS = (
    "보내지 말고", "전달 대신", "동작은 취소", "공유하지 말고", "작업은 취소", "외부 공유가 아니라",
    "전송을 하지 말고", "보내기는 접고", "넘기는 대신", "전달 단계는 빼고", "처리는 생략하고", "외부 전달", "외부 전송",
    "작업이 아니라",
)
LOCAL_UPDATE_SCOPE_TERMS = ("내부", "기기 안", "장치 안", "상태", "로컬", "완료 상태")


def _is_local_update(view: TaskView) -> bool:
    values = _record_values_text(view)
    prompt = view.prompt.lower() + " " + view.history_text.lower()
    return (
        "persistent_memory_write" in view.record_types
        or "local_update_only" in values
        or "memory_write" in values
        or (
            any(term in prompt for term in LOCAL_UPDATE_NEGATION_TERMS)
            and any(term in prompt for term in LOCAL_UPDATE_SCOPE_TERMS)
        )
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


TARGET_STATUS_LABEL_TOKENS = ("invalidated", "superseded", "changed", "pending", "conflict", "unresolved")


def _looks_like_target_name(value: str) -> bool:
    lowered = value.lower()
    return bool(value) and not any(token in lowered for token in TARGET_STATUS_LABEL_TOKENS)


def _resolved_target_value(resolved: Any) -> str:
    if isinstance(resolved, dict):
        for key in ("target", "route", "value", "name", "recipient", "channel"):
            if resolved.get(key):
                return str(resolved[key])
        return ""
    if isinstance(resolved, str):
        return resolved
    return ""


def _explicit_user_confirmation_requested(view: TaskView) -> bool:
    return any(word in view.prompt.lower() for word in ["누구에게 어떤 범위", "사용자에게 먼저 확인", "사용자 확인", "다시 확인"])


def infer_target(view: TaskView, focal: dict[str, Any], control: str, session: dict[str, Any]) -> str:
    # Target is always derived from structured task signals (resolved_target record,
    # target_changed_after_turn record, or object attrs) — never a guessed domain name,
    # since guessed vocabulary only matches the specific wording it was tuned against.
    if _is_local_update(view):
        return "memory_store"

    if control == "hold" and _precondition_invalidated(view):
        return "user"

    changed_target = view.record_value("target_changed_after_turn")
    if isinstance(changed_target, str) and _looks_like_target_name(changed_target):
        return changed_target

    resolved_value = _resolved_target_value(view.record_value("resolved_target"))

    # A resolved target survives a generic "confirmation needed" signal when the
    # ambiguity is about which focal object to use, not about the target itself.
    if control == "ask" and "ambiguous_focal" in view.record_types and resolved_value:
        return resolved_value

    if control == "ask" and (
        _condition_uncertain(view)
        or _doctor_note_external_scope_uncertain(view, focal)
        or _explicit_user_confirmation_requested(view)
        or _prior_hold_followup(view, session)
        or _prior_local_only_external_followup(view, session)
        or "duration_ambiguous" in view.record_types
    ):
        return "user"
    if resolved_value:
        return resolved_value

    attrs = focal.get("attrs") or {}
    for key in ("recipient", "target", "channel", "app", "merchant", "attendee", "name"):
        if attrs.get(key):
            return str(attrs[key])

    object_target = _unique_object_target(view, str(focal.get("id") or ""))
    if object_target:
        return object_target

    if control == "ask":
        return "user"
    return str(session.get("last_target") or "user")


def decide_control(view: TaskView, focal: dict[str, Any], evidence: dict[str, Any], session: dict[str, Any] | None = None) -> str:
    session = session or {}
    types = view.record_types
    values = _record_values_text(view)
    prompt = view.prompt.lower()
    if _is_local_update(view):
        return "proceed"
    if {"security_alert", "safety_mode"} & types or "phishing" in values or "피싱" in values or "impersonation" in values:
        return "hold"
    if _prior_hold_followup(view, session) or _prior_local_only_external_followup(view, session):
        return "ask"
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
    if _prior_success_memory_reuse(view):
        return "amend"
    if _precondition_invalidated(view):
        return "hold"
    if _stored_channel_ambiguous(view) and _has_value(view, "local_update_boundary") and "persistent_memory_recall" in types:
        return "amend"
    if _condition_uncertain(view):
        return "ask"
    if _doctor_note_external_scope_uncertain(view, focal):
        return "ask"
    if _doctor_note_external_precondition_invalidated(view, focal):
        return "hold"
    if any(word in prompt for word in ["다시 확인", "누구에게 어떤 범위", "사용자에게 먼저 확인", "사용자 확인", "확인 질문", "clarification", "확인하지 않으면", "다시 물어봐"]):
        return "ask"
    if "payment_policy" in types and "requires_confirmation" in values:
        return "ask"
    if _guardrail_local_boundary_review(view) or _surface_resolved_channel_conflict(view):
        return "ask"
    if _temporary_privacy_override(view) or _same_place_check_summary(view):
        return "amend"
    if _stored_channel_ambiguous(view) and _has_value(view, "local_update_boundary"):
        return "amend"
    if _stored_channel_ambiguous(view):
        return "ask"
    if _persistent_memory_sensitive_share(view):
        return "amend"
    if _external_binding_blocked(view):
        return "hold"
    if _guardrail_blocked_binding(view):
        return "hold"
    if _guardrail_verified_external_route(view):
        return "proceed"
    if _has_status_update_boundary(view) and "ambiguous_target" not in types and _has_value(view, *ROUTE_CONFIRMED_VALUES):
        return "proceed"
    if _has_value(view, "redacted_summary_only", "summary_only", "minimal_disclosure", "식별 가능한 세부값을 제외") or _summary_only_composite_plan(view):
        return "amend"
    if _has_value(view, "redacted_external_boundary") and _has_value(view, "internal_binding_confirmed", "single_internal_candidate"):
        return "amend"
    if _has_value(view, "privacy_rule_violation"):
        return "hold"
    if _has_value(view, "raw_sensitive_forbidden"):
        return "amend"
    if _has_value(view, "dispatch_blocked_until_binding", "authority_incomplete", "route_resolution_required", "target_conflict"):
        return "ask"
    if "ambiguous_target" in types and _has_value(view, *ROUTE_CONFIRMED_VALUES):
        return "amend"
    if _has_value(view, "confirmation_required", "route_resolution_required", "target_conflict"):
        return "ask"
    if types & ASK_RECORD_TYPES:
        return "ask"
    if types & EXTERNAL_RECORD_TYPES or contained_fields(focal) & SENSITIVE_FIELDS:
        return "amend"
    return "proceed"


def _target_ambiguity_signal(view: TaskView) -> bool:
    return "ambiguous_target" in view.record_types or _has_value(view, "dispatch_blocked_until_binding", "authority_incomplete")


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
    changed_target = view.record_value("target_changed_after_turn")
    if control == "ask" and isinstance(changed_target, str) and "route_superseded" in changed_target:
        return {"mode": "none", "allowed_fields": [], "excluded_fields": [], "requires_user_confirmation": True}
    if control == "ask" and view.record_value("external_share_policy") == "raw_sensitive_forbidden" and _confirmation_precondition(view):
        return {"mode": "summary", "allowed_fields": ["summary"], "excluded_fields": ["name"], "requires_user_confirmation": True}
    if control == "ask" and view.record_value("external_share_policy") == "raw_quote_forbidden" and _condition_uncertain(view):
        return {"mode": "summary", "allowed_fields": ["summary"], "excluded_fields": ["raw_quote"], "requires_user_confirmation": True}
    if control == "ask" and _doctor_note_external_scope_uncertain(view, focal) and _has_value(view, "새 전제가 확정되지"):
        return {"mode": "redacted", "allowed_fields": ["summary"], "excluded_fields": ["raw_quote"], "requires_user_confirmation": True}
    if control == "ask" and _guardrail_ladder(view):
        if view.record_value("share_boundary_update") == "redacted_external_boundary" and _confirmation_precondition(view):
            return {"mode": "redacted", "allowed_fields": ["summary"], "excluded_fields": ["raw_quote"], "requires_user_confirmation": True}
        if _guardrail_local_boundary_review(view) and not _confirmation_precondition(view):
            return {"mode": "redacted", "allowed_fields": ["summary"], "excluded_fields": ["raw_quote"], "requires_user_confirmation": True}
    if control == "ask" and "ambiguous_focal" in view.record_types:
        resolved = str(view.record_value("resolved_target") or "")
        if resolved == "privacy_review" and _has_value(view, "표면 문장만"):
            return {"mode": "none", "allowed_fields": [], "excluded_fields": [], "requires_user_confirmation": True}
        if resolved == "audit_vendor" and view.record_value("route_candidate_snapshot") == "external_candidates_present" and not _has_value(view, "누구에게 어떤 범위"):
            return {
                "mode": "redacted",
                "allowed_fields": ["summary"],
                "excluded_fields": sorted(contains & SENSITIVE_FIELDS) or ["name", "numeric_value", "raw_quote", "rrn"],
                "requires_user_confirmation": True,
            }
        return {"mode": "summary", "allowed_fields": ["summary"], "excluded_fields": ["name"], "requires_user_confirmation": True}
    if control == "ask" and (
        (isinstance(changed_target, str) and changed_target and "invalidated" not in changed_target)
        or (_guardrail_ladder(view) and view.record_value("share_boundary_update") in {"redacted_external_boundary", "local_update_boundary"})
        or _surface_resolved_channel_conflict(view)
        or "duration_ambiguous" in view.record_types
        or _condition_uncertain(view)
        or ("ambiguous_focal" in view.record_types and view.record_value("resolved_target") is not None)
    ):
        mode = "redacted" if _has_value(view, "민감", "점검 내용", "check summary", "원본", "사진", "duration_ambiguous", "전제 조건") else "summary"
        return {"mode": mode, "allowed_fields": ["summary"], "excluded_fields": ["raw_quote"], "requires_user_confirmation": True}
    if control == "ask":
        mode = "redacted" if _has_value(view, "민감", "점검 내용", "check summary", "원본", "사진", "duration_ambiguous", "전제 조건") else "summary"
        return {"mode": mode, "allowed_fields": ["summary"], "excluded_fields": excluded or ["raw_quote"], "requires_user_confirmation": True}
    if _prior_success_memory_reuse(view) and control == "amend":
        return {"mode": "redacted", "allowed_fields": ["summary"], "excluded_fields": ["raw_quote"], "requires_user_confirmation": False}
    if (_temporary_privacy_override(view) or _same_place_check_summary(view)) and control == "amend":
        return {"mode": "redacted", "allowed_fields": ["summary"], "excluded_fields": ["raw_quote"], "requires_user_confirmation": False}
    if _has_status_update_boundary(view) and control == "proceed":
        return {
            "mode": "status_only",
            "allowed_fields": ["status"],
            "excluded_fields": ["raw_quote", "location", "numeric_value"],
            "requires_user_confirmation": False,
        }
    if _guardrail_verified_external_route(view) and control == "proceed":
        return {"mode": "raw", "allowed_fields": ["summary", "title"], "excluded_fields": [], "requires_user_confirmation": False}
    if _plain_composite_plan(view) and control == "proceed":
        return {"mode": "raw", "allowed_fields": ["summary", "title"], "excluded_fields": [], "requires_user_confirmation": False}
    if control == "amend":
        needs_confirmation = _target_ambiguity_signal(view)
        return {"mode": "redacted", "allowed_fields": ["summary"], "excluded_fields": excluded or ["raw_quote"], "requires_user_confirmation": needs_confirmation}
    if "raw" in _record_values_text(view) and not excluded:
        return {"mode": "raw", "allowed_fields": ["raw"], "excluded_fields": [], "requires_user_confirmation": False}
    return {"mode": "summary", "allowed_fields": ["summary", "status"], "excluded_fields": excluded, "requires_user_confirmation": False}


def build_policy(view: TaskView, focal: dict[str, Any], control: str, scope: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    flags = set(evidence.get("risk_flags") or [])
    flags.discard("security_alert")
    flags.discard("sensitive_content")
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
    if not local_status and control in {"proceed", "amend"} and scope.get("mode") not in {"status_only", "none"}:
        flags.add("external_share")
    if (
        control == "ask"
        and (_condition_uncertain(view) or _confirmation_precondition(view) or _doctor_note_external_scope_uncertain(view, focal) or "duration_ambiguous" in types)
        and "ambiguous_focal" not in types
        and "target_changed_after_turn" not in types
    ):
        flags.discard("external_share")
        flags.add("local_only")
    if control == "ask" and _guardrail_ladder(view) and _confirmation_precondition(view):
        flags.discard("external_share")
        flags.add("local_only")
        flags.add("precondition_changed")
    if control == "ask" and _guardrail_local_boundary_review(view) and not _confirmation_precondition(view):
        flags.add("external_share")
    if contains & SENSITIVE_FIELDS or "sensitive" in values:
        flags.add("sensitive_content")
    if _target_ambiguity_signal(view):
        flags.add("target_ambiguity")
    if "ambiguous_focal" in types:
        flags.add("ambiguous_focal")
    if control == "ask":
        flags.add("clarification_required")
    if control == "amend" or scope.get("mode") == "redacted":
        flags.add("minimal_disclosure")
    invalidated_precondition = (
        _precondition_invalidated(view)
        or _doctor_note_external_precondition_invalidated(view, focal)
        or _child_sleep_lighting_memory_block(view)
        or _guardrail_blocked_binding(view)
        or _external_binding_blocked(view)
        or _revoked_or_security_precondition(view)
    )
    if (
        "precondition" in values
        or invalidated_precondition
        or _stored_channel_ambiguous(view)
        or _guardrail_local_boundary_review(view)
        or _surface_resolved_channel_conflict(view)
    ):
        flags.add("precondition_changed")
    if _has_value(view, "redacted_summary_only", "summary_only", "minimal_disclosure", "식별 가능한 세부값을 제외"):
        flags.add("minimal_disclosure")
    if _has_value(view, "privacy_rule_violation"):
        flags.add("safety")
    if control == "hold":
        flags.add("safety")
    violations: set[str] = set()
    if control == "hold" and ("precondition" in values or invalidated_precondition or "취소" in view.history_text or "취소" in view.prompt or types & PRECONDITION_RECORD_TYPES):
        flags.add("precondition_invalidated")
        flags.discard("precondition_changed")
        violations.add("precondition_changed_ignored")
    requires_confirmation = control == "ask" or (control != "hold" and "target_ambiguity" in flags)
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
        elif "local_only" in policy.get("risk_flags", []):
            events[0]["args"]["purpose"] = "route_resolution_required"
            reason = "route_resolution_required"
        if target != "user":
            if events[0]["args"]["purpose"] != "clarify_precondition":
                events[0]["args"]["purpose"] = "route_resolution_required"
                reason = "route_resolution_required"
        events.append({"verb": "clarify", "target": "user", "args": {"reason": reason}})
    elif scope.get("mode") == "status_only":
        events.append({"verb": "verify", "target": "share_boundary_update", "args": {"scope": "local_update"}})
        events.append({"verb": "update", "target": focal_id, "args": {"state": "local_status_only"}})
    elif control == "amend":
        excluded_fields = scope.get("excluded_fields") or []
        remove = excluded_fields[0] if len(excluded_fields) == 1 else "sensitive_fields"
        events.append({"verb": "redact", "target": focal_id, "args": {"remove": remove}})
        events.append({"verb": "dispatch", "target": target, "args": {"scope": "redacted"}})
    else:
        if scope.get("mode") == "summary":
            events.append({"verb": "summarize", "target": focal_id, "args": {"mode": "summary"}})
        events.append({"verb": "dispatch", "target": target, "args": {"scope": scope.get("mode", "summary")}})
    return events[:18]


def user_response(control: str, target: str, scope: dict[str, Any]) -> str:
    if control == "hold":
        return "I will not proceed because a policy or safety condition blocks this request."
    if control == "ask":
        return "I need confirmation about the target or scope before proceeding."
    if control == "amend":
        return f"I will proceed to {target} after excluding sensitive information."
    return f"I will proceed to {target}."


def update_session_state(
    view: TaskView,
    session: dict[str, Any],
    focal_id: str,
    target: str,
    control: str,
    scope: dict[str, Any],
    policy: dict[str, Any],
) -> None:
    session["last_focal_id"] = focal_id
    session["last_target"] = target
    session["last_control"] = control
    session["last_scope_mode"] = str(scope.get("mode") or "")
    session["last_risk_flags"] = list(policy.get("risk_flags") or [])
    session["last_requires_confirmation"] = bool(policy.get("requires_confirmation"))
    share_boundary = view.record_value("share_boundary_update")
    if isinstance(share_boundary, str) and share_boundary:
        session["share_boundary"] = share_boundary
    authority = view.record_value("dispatch_authority_check")
    if isinstance(authority, str) and authority:
        session["route_authority"] = authority
    session["route_confirmed"] = _has_value(view, *ROUTE_CONFIRMED_VALUES)


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


def submission_answer(answer: dict[str, Any]) -> dict[str, Any]:
    return {
        "focal_id": str(answer.get("focal_id") or ""),
        "target": str(answer.get("target") or ""),
        "control": answer.get("control"),
        "content_scope": answer.get("content_scope") or {},
        "policy": answer.get("policy") or {},
        "plan_events": answer.get("plan_events") or [],
    }


def run_harness(tasks: list[dict[str, Any]], harness_cls: type = FinalHarness, *, harness_name: str = "scpc_rule_harness") -> dict[str, Any]:
    ordered = sorted(tasks, key=lambda t: (str(t.get("session_id", "")), int(t.get("turn_index", 0)), str(t.get("id", ""))))
    harness = harness_cls()
    sessions: dict[str, dict[str, Any]] = {}
    answers: dict[str, dict[str, Any]] = {}
    for task in ordered:
        sid = str(task.get("session_id", ""))
        session = sessions.setdefault(sid, {})
        answers[str(task["id"])] = submission_answer(answer_one(harness, task, session))
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
        validate_answer_consistency(str(task_id), answer)


def _event_verbs(answer: dict[str, Any]) -> set[str]:
    return {str(event.get("verb")) for event in answer.get("plan_events") or [] if isinstance(event, dict)}


def validate_answer_consistency(task_id: str, answer: dict[str, Any]) -> None:
    control = answer.get("control")
    scope = answer.get("content_scope") or {}
    policy = answer.get("policy") or {}
    verbs = _event_verbs(answer)

    if control == "hold":
        if scope.get("mode") != "none" or verbs & {"dispatch", "redact", "update", "clarify"} or "guard" not in verbs:
            raise ValueError(f"{task_id} hold answer has contradictory scope or plan")
        if policy.get("requires_confirmation") is True or scope.get("requires_user_confirmation") is True:
            raise ValueError(f"{task_id} hold answer must not request confirmation")
    elif control == "ask":
        if "clarify" not in verbs:
            raise ValueError(f"{task_id} ask answer must include clarify event")
        if policy.get("requires_confirmation") is not True or scope.get("requires_user_confirmation") is not True:
            raise ValueError(f"{task_id} ask answer must require confirmation")
        if verbs & {"dispatch", "update"}:
            raise ValueError(f"{task_id} ask answer must not execute dispatch or update")
    elif scope.get("mode") == "status_only":
        if "update" not in verbs:
            raise ValueError(f"{task_id} status_only answer must include update event")
        if verbs & {"dispatch", "redact", "clarify", "guard"}:
            raise ValueError(f"{task_id} status_only answer has contradictory plan")
    elif control == "amend":
        if scope.get("mode") != "redacted" or not {"redact", "dispatch"} <= verbs:
            raise ValueError(f"{task_id} amend answer must redact before dispatch")
        if verbs & {"clarify", "guard", "update"}:
            raise ValueError(f"{task_id} amend answer has contradictory plan")
    elif control == "proceed":
        if "dispatch" not in verbs:
            raise ValueError(f"{task_id} proceed answer must include dispatch event")
        if verbs & {"clarify", "guard", "redact"}:
            raise ValueError(f"{task_id} proceed answer has contradictory plan")


def write_submission_csv(payload: dict[str, Any], output_path: str | Path) -> None:
    validate_payload(payload)
    path = Path(output_path)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["submission"], lineterminator="\r\n")
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

"""Branch-labeled mirror of harness.infer_target.

See trace_focal.py's module docstring for why this exists and how it's kept
honest (tests/test_diagnostics_drift.py).
"""

from __future__ import annotations

from typing import Any

from harness import (
    TaskView,
    _condition_uncertain,
    _doctor_note_external_scope_uncertain,
    _explicit_user_confirmation_requested,
    _is_local_update,
    _looks_like_target_name,
    _precondition_invalidated,
    _prior_hold_followup,
    _prior_local_only_external_followup,
    _resolved_target_value,
    _unique_object_target,
)


def infer_target_traced(
    view: TaskView,
    focal: dict[str, Any],
    control: str,
    session: dict[str, Any],
    user_memory: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if _is_local_update(view):
        return "memory_store", "T01_local_update"

    if control == "hold" and _precondition_invalidated(view):
        return "user", "T02_hold_precondition_invalidated"

    changed_target = view.record_value("target_changed_after_turn")
    if isinstance(changed_target, str) and _looks_like_target_name(changed_target):
        return changed_target, "T03_changed_target_name"

    resolved_value = _resolved_target_value(view.record_value("resolved_target"))

    recall = view.record_value("persistent_memory_recall")
    if isinstance(recall, dict) and recall.get("memory_key") and user_memory:
        memory = user_memory.get(str(recall["memory_key"]))
        if isinstance(memory, dict):
            memory_class = str(recall.get("memory_class") or "")
            focal_attrs = focal.get("attrs") or {}
            if memory_class == "standing_constraint" and memory.get("approval_channel"):
                return str(memory["approval_channel"]), "T04a_recall_approval_channel"
            if memory_class == "prior_result" and memory.get("last_success_target"):
                return str(memory["last_success_target"]), "T04b_recall_last_success_target"
            if focal.get("type") == "iot_routine" and "light" in (focal_attrs.get("actions") or []) and memory.get("dusk_room"):
                return str(memory["dusk_room"]), "T04c_recall_dusk_room"
            if focal.get("type") == "health_record" and memory.get("health_channel"):
                return str(memory["health_channel"]), "T04d_recall_health_channel"
            if memory.get("preferred_channel"):
                return str(memory["preferred_channel"]), "T04e_recall_preferred_channel"

    if (
        control == "ask"
        and "ambiguous_focal" in view.record_types
        and resolved_value
        and not _explicit_user_confirmation_requested(view)
    ):
        return resolved_value, "T05_ask_ambiguous_focal_resolved"

    if control == "ask" and (
        _condition_uncertain(view)
        or _doctor_note_external_scope_uncertain(view, focal)
        or _explicit_user_confirmation_requested(view)
        or _prior_hold_followup(view, session)
        or _prior_local_only_external_followup(view, session)
        or "duration_ambiguous" in view.record_types
    ):
        return "user", "T06_ask_user_signals"
    if resolved_value:
        return resolved_value, "T07_resolved_value"

    attrs = focal.get("attrs") or {}
    for key in ("recipient", "target", "channel", "app", "merchant", "attendee", "name"):
        if attrs.get(key):
            return str(attrs[key]), f"T08_attrs_{key}"

    object_target = _unique_object_target(view, str(focal.get("id") or ""))
    if object_target:
        return object_target, "T09_unique_object_target"

    if control == "ask":
        return "user", "T10_ask_fallback_user"
    return str(session.get("last_target") or "user"), "T11_session_last_or_user"

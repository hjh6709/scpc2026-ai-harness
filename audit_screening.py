"""Observability tool for the harness's decision logic.

Runs the harness against a task set (normally screening_tasks.jsonl, which has
no reference answers) and reports internal signals that reference answers can't
show directly:

  1. Rule coverage - how often each internal gate function fires. A function
     that never fires is either (a) genuinely rare/absent in this task set, or
     (b) blind to phrasing/values this task set actually uses. Cross-check
     candidates in (b) against dev_answers.json before trusting a fix.
  2. Correction-clause consistency - tasks sharing the same trailing "단, ..."
     clause should mostly resolve to the same control. A clause split roughly
     evenly across 3-4 controls usually means a missed phrasing variant
     upstream, not legitimate diversity.
  3. Novel record values - values that appear for a record type in this task
     set but never appear in dev_tasks.jsonl for that same type. Any code path
     that does exact-string matching against a fixed value list will silently
     skip these.

None of this tells you whether an answer is *correct* - there is no reference
for screening. It only tells you where the harness's own logic might be
blind, so you know what to go read by hand.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import harness as H
from harness import FinalHarness, TaskView, VALID_CONTROLS, VALID_SCOPE_MODES, choose_focal, load_jsonl, run_harness

GATE_FUNCTIONS_VIEW = [
    "_precondition_invalidated", "_child_sleep_lighting_memory_block", "_confirmation_precondition",
    "_condition_uncertain", "_stored_channel_ambiguous", "_persistent_memory_sensitive_share",
    "_prior_success_memory_reuse", "_enterprise_policy_review", "_temporary_privacy_override",
    "_same_place_check_summary", "_guardrail_ladder", "_guardrail_blocked_binding",
    "_mixed_local_external_confirmation", "_external_binding_blocked", "_guardrail_verified_external_route",
    "_guardrail_local_boundary_review", "_surface_resolved_channel_conflict", "_summary_only_composite_plan",
    "_plain_composite_plan", "_revoked_or_security_precondition", "_same_context_followup",
    "_direct_reuse_followup", "_is_local_update", "_has_status_update_boundary",
    "_explicit_user_confirmation_requested", "_target_ambiguity_signal",
]
GATE_FUNCTIONS_VIEW_FOCAL = ["_doctor_note_external_precondition_invalidated", "_doctor_note_external_scope_uncertain"]
GATE_FUNCTIONS_VIEW_SESSION = ["_prior_hold_followup", "_prior_local_only_external_followup"]

KEY_VALUE_RECORD_TYPES = [
    "route_candidate_snapshot", "dispatch_authority_check", "share_boundary_update",
    "external_share_policy", "ambiguous_target", "ambiguous_focal", "consent",
    "guardrail_ladder_signal", "route_binding_order", "target_changed_after_turn",
    "enterprise_policy_recall", "payment_policy", "health_share_policy", "memory_conflict",
    "ops_memory_recall", "safety_mode",
]


def rule_coverage(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {name: 0 for name in GATE_FUNCTIONS_VIEW + GATE_FUNCTIONS_VIEW_FOCAL + GATE_FUNCTIONS_VIEW_SESSION}
    for t in tasks:
        view = TaskView(t)
        focal = choose_focal(view)
        for name in GATE_FUNCTIONS_VIEW:
            if getattr(H, name)(view):
                counts[name] += 1
        for name in GATE_FUNCTIONS_VIEW_FOCAL:
            if getattr(H, name)(view, focal):
                counts[name] += 1
        for name in GATE_FUNCTIONS_VIEW_SESSION:
            if getattr(H, name)(view, {}):
                counts[name] += 1
    return counts


def clause_consistency(tasks: list[dict[str, Any]], payload: dict[str, Any], min_n: int = 3) -> list[tuple[float, int, str, dict[str, int]]]:
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in tasks:
        m = re.search(r"단,\s*(.+)$", t.get("prompt", ""))
        if m:
            clusters[m.group(1).strip()].append(t)
    rows = []
    for clause, members in clusters.items():
        if len(members) < min_n:
            continue
        controls = Counter(payload["answers"][t["id"]]["control"] for t in members)
        top = controls.most_common(1)[0]
        rows.append((top[1] / len(members), len(members), clause, dict(controls)))
    rows.sort()
    return rows


def novel_record_values(tasks: list[dict[str, Any]], dev_tasks: list[dict[str, Any]]) -> dict[str, list[tuple[str, int]]]:
    def values_by_type(dataset: list[dict[str, Any]]) -> dict[str, Counter]:
        out: dict[str, Counter] = defaultdict(Counter)
        for t in dataset:
            for r in (t.get("device_state") or {}).get("records") or []:
                rt = r.get("type")
                if rt in KEY_VALUE_RECORD_TYPES and isinstance(r.get("value"), str):
                    out[rt][r["value"]] += 1
        return out

    dev_vals = values_by_type(dev_tasks)
    task_vals = values_by_type(tasks)
    novel: dict[str, list[tuple[str, int]]] = {}
    for rt in KEY_VALUE_RECORD_TYPES:
        dv = set(dev_vals.get(rt, {}))
        tv = task_vals.get(rt, Counter())
        found = [(v, c) for v, c in tv.items() if v not in dv]
        if found:
            novel[rt] = sorted(found, key=lambda x: -x[1])
    return novel


def exception_sweep(tasks: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Run every task through the harness in session/turn order, one at a time,
    and collect (task_id, error) for every exception - instead of run_harness's
    default behavior of aborting on the first one. A clean sweep is the only
    way to be sure no task in this set can crash the harness at all."""
    harness = FinalHarness()
    ordered = sorted(tasks, key=lambda t: (str(t.get("session_id", "")), int(t.get("turn_index", 0)), str(t.get("id", ""))))
    sessions: dict[str, dict[str, Any]] = {}
    failures: list[tuple[str, str]] = []
    for t in ordered:
        sid = str(t.get("session_id", ""))
        session = sessions.setdefault(sid, {})
        try:
            harness.answer_task(t, session)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, this is a sweep
            failures.append((str(t.get("id", "?")), f"{type(exc).__name__}: {exc}"))
    return failures


def shape_invariants(payload: dict[str, Any]) -> list[str]:
    """Full sweep (not a sample) of every submitted answer's shape, beyond what
    validate_payload already enforces at generation time - this exists so the
    check itself is visible and independently re-runnable."""
    problems: list[str] = []
    for task_id, answer in payload["answers"].items():
        if answer.get("control") not in VALID_CONTROLS:
            problems.append(f"{task_id}: invalid control {answer.get('control')!r}")
        scope = answer.get("content_scope") or {}
        if scope.get("mode") not in VALID_SCOPE_MODES:
            problems.append(f"{task_id}: invalid scope mode {scope.get('mode')!r}")
        events = answer.get("plan_events") or []
        if len(events) > 18:
            problems.append(f"{task_id}: {len(events)} plan_events, exceeds 18")
        for i, ev in enumerate(events):
            if not ev.get("verb") or not isinstance(ev.get("verb"), str):
                problems.append(f"{task_id}: event {i} has empty/non-str verb")
            if not isinstance(ev.get("target"), str):
                problems.append(f"{task_id}: event {i} target is not a str")
            if not isinstance(ev.get("args"), dict):
                problems.append(f"{task_id}: event {i} args is not a dict")
    return problems


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the harness's decision coverage on an unlabeled task set.")
    parser.add_argument("--tasks", required=True, help="Path to screening_tasks.jsonl (or any unlabeled task JSONL).")
    parser.add_argument("--dev-tasks", default=None, help="Path to dev_tasks.jsonl, for the novel-value comparison. Optional.")
    parser.add_argument("--min-clause-n", type=int, default=3, help="Minimum cluster size to report in the clause consistency section.")
    parser.add_argument("--consistency-threshold", type=float, default=0.85, help="Flag clause clusters below this single-control consistency.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tasks = load_jsonl(args.tasks)

    print(f"=== Exception sweep ({len(tasks)} tasks, one task at a time) ===")
    failures = exception_sweep(tasks)
    if failures:
        print(f"  {len(failures)} task(s) raised an exception:")
        for task_id, error in failures[:20]:
            print(f"    {task_id}: {error}")
    else:
        print("  none - every task produced an answer without raising")

    payload = run_harness(tasks, harness_name="audit")

    print("\n=== Answer shape invariants (full sweep, not a sample) ===")
    problems = shape_invariants(payload)
    if problems:
        print(f"  {len(problems)} problem(s):")
        for p in problems[:20]:
            print(f"    {p}")
    else:
        print(f"  none - all {len(payload['answers'])} answers pass control/scope/plan_events shape checks")

    print(f"\n=== Rule coverage ({len(tasks)} tasks) ===")
    counts = rule_coverage(tasks)
    for name, c in sorted(counts.items(), key=lambda x: x[1]):
        flag = "  <-- ZERO HITS, investigate" if c == 0 else ""
        print(f"  {name:45s} {c:6d}{flag}")

    print(f"\n=== Correction-clause consistency (clusters with n>={args.min_clause_n}) ===")
    rows = clause_consistency(tasks, payload, args.min_clause_n)
    flagged = [r for r in rows if r[0] < args.consistency_threshold]
    print(f"  {len(rows)} clusters total, {len(flagged)} below {args.consistency_threshold:.0%} consistency")
    for consistency, n, clause, controls in flagged:
        print(f"  n={n:3d} consistency={consistency:.0%} controls={controls}")
        print(f"       {clause[:100]}")

    if args.dev_tasks:
        print("\n=== Novel record values (present here, absent in dev) ===")
        dev_tasks = load_jsonl(args.dev_tasks)
        novel = novel_record_values(tasks, dev_tasks)
        if not novel:
            print("  none found")
        for rt, values in novel.items():
            total = sum(c for _, c in values)
            print(f"  {rt}: {total} occurrences across {len(values)} novel value(s)")
            for v, c in values:
                print(f"       {v!r}: {c}")


if __name__ == "__main__":
    main()

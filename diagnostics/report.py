"""Branch-distribution report for focal/control/target resolution.

Replaces the one-off "instrument a function, run it over screening, print a
Counter" scripts written ad hoc throughout development - same idea, but
session-threaded correctly (like run_harness) and kept in sync with harness.py
by tests/test_diagnostics_drift.py.

Usage:
    python3 diagnostics/report.py --tasks "/path/to/screening_tasks.jsonl"
    python3 diagnostics/report.py --tasks screening_tasks.jsonl --branch L01_is_local_update --field control --sample 5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from diagnostics.trace_control import decide_control_traced
from diagnostics.trace_focal import choose_focal_traced
from diagnostics.trace_target import infer_target_traced
from harness import (
    TaskView,
    build_content_scope,
    build_policy,
    load_jsonl,
    update_session_memory,
    update_session_state,
)


def run_traced(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Session-threaded replay (matching run_harness's ordering) that records
    which branch fired for focal/control/target on every task."""
    ordered = sorted(
        tasks,
        key=lambda t: (str(t.get("session_id", "")), int(t.get("turn_index", 0)), str(t.get("id", ""))),
    )
    sessions: dict[str, dict[str, Any]] = {}
    user_memory: dict[str, Any] = {}
    rows = []
    for task in ordered:
        session_id = str(task.get("session_id", ""))
        session = sessions.setdefault(session_id, {})
        view = TaskView(task)

        focal, focal_branch = choose_focal_traced(view)
        control, control_branch = decide_control_traced(view, focal, {}, session)
        target, target_branch = infer_target_traced(view, focal, control, session, user_memory)
        scope = build_content_scope(view, focal, control, {})
        policy = build_policy(view, focal, control, scope, {})

        rows.append(
            {
                "id": task["id"],
                "focal_branch": focal_branch,
                "control": control,
                "control_branch": control_branch,
                "target": target,
                "target_branch": target_branch,
                "scope_mode": scope["mode"],
                "risk_flags": policy["risk_flags"],
            }
        )

        update_session_state(view, session, focal.get("id", ""), target, control, scope, policy)
        update_session_memory(view, session, user_memory)
    return rows


def print_distribution(rows: list[dict[str, Any]]) -> None:
    total = len(rows)
    for label, key in [("focal", "focal_branch"), ("control", "control_branch"), ("target", "target_branch")]:
        counts = Counter(r[key] for r in rows)
        print(f"=== {label} branch distribution ({total} tasks) ===")
        for branch, n in counts.most_common():
            print(f"  {n:4d} ({100 * n / total:5.1f}%)  {branch}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", required=True, help="Path to a task JSONL file (dev_tasks.jsonl or screening_tasks.jsonl).")
    parser.add_argument("--branch", help="Filter to rows whose focal_branch/control_branch/target_branch equals this value.")
    parser.add_argument("--field", choices=["focal_branch", "control_branch", "target_branch"], default="control_branch", help="Which branch field --branch filters on.")
    parser.add_argument("--sample", type=int, default=0, help="Print this many matching task ids (with --branch) for follow-up inspection.")
    args = parser.parse_args()

    tasks = load_jsonl(args.tasks)
    rows = run_traced(tasks)

    if args.branch:
        matches = [r for r in rows if r[args.field] == args.branch]
        print(f"{len(matches)} / {len(rows)} tasks have {args.field}=={args.branch!r}")
        for r in matches[: args.sample or len(matches)]:
            print(" ", r["id"], json.dumps(r, ensure_ascii=False))
        return

    print_distribution(rows)


if __name__ == "__main__":
    main()

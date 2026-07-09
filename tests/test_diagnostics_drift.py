"""Guards against diagnostics/trace_*.py silently drifting from harness.py.

The trace_* modules are hand-maintained mirrors of choose_focal/decide_control/
infer_target that also report which internal branch fired - useful for
audit_screening.py-style reporting, but only trustworthy if they actually
compute the same answer as the real functions. This session hit that exact
failure mode once already (a stale rule_coverage() session-isolation bug
produced misleading "0 hits" results), so this test replays the real task set
through both the real and traced functions and fails loudly the moment they
disagree, forcing whoever changed harness.py's decision logic to update the
matching trace_*.py too.

Skips (doesn't fail) when the competition data isn't available locally, since
the task data isn't committed to this repo.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from diagnostics.trace_control import decide_control_traced
from diagnostics.trace_focal import choose_focal_traced
from diagnostics.trace_target import infer_target_traced
from harness import (
    TaskView,
    build_content_scope,
    build_policy,
    choose_focal,
    decide_control,
    infer_target,
    load_jsonl,
    update_session_memory,
    update_session_state,
)


def _find_data_dir() -> Path | None:
    candidates = []
    env = os.environ.get("SCPC_DATA_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/Users/hanjeonghyun/Downloads/data 3"))
    for candidate in candidates:
        if (candidate / "screening_tasks.jsonl").exists():
            return candidate
    return None


DATA_DIR = _find_data_dir()


@unittest.skipUnless(DATA_DIR is not None, "competition data not available locally")
class TracedFunctionsMatchRealFunctionsTests(unittest.TestCase):
    def test_traced_outputs_match_real_outputs_on_screening_tasks(self):
        tasks = load_jsonl(DATA_DIR / "screening_tasks.jsonl")
        ordered = sorted(
            tasks,
            key=lambda t: (str(t.get("session_id", "")), int(t.get("turn_index", 0)), str(t.get("id", ""))),
        )
        sessions: dict[str, dict] = {}
        user_memory: dict = {}
        focal_mismatches = []
        control_mismatches = []
        target_mismatches = []

        for task in ordered:
            session_id = str(task.get("session_id", ""))
            session = sessions.setdefault(session_id, {})
            view = TaskView(task)

            real_focal = choose_focal(view)
            traced_focal, _focal_branch = choose_focal_traced(view)
            if real_focal != traced_focal:
                focal_mismatches.append(task["id"])

            real_control = decide_control(view, real_focal, {}, session)
            traced_control, _control_branch = decide_control_traced(view, real_focal, {}, session)
            if real_control != traced_control:
                control_mismatches.append(task["id"])

            real_target = infer_target(view, real_focal, real_control, session, user_memory)
            traced_target, _target_branch = infer_target_traced(view, real_focal, real_control, session, user_memory)
            if real_target != traced_target:
                target_mismatches.append(task["id"])

            scope = build_content_scope(view, real_focal, real_control, {})
            policy = build_policy(view, real_focal, real_control, scope, {})
            update_session_state(view, session, real_focal.get("id", ""), real_target, real_control, scope, policy)
            update_session_memory(view, session, user_memory)

        self.assertEqual(focal_mismatches, [], "diagnostics/trace_focal.py drifted from choose_focal")
        self.assertEqual(control_mismatches, [], "diagnostics/trace_control.py drifted from decide_control")
        self.assertEqual(target_mismatches, [], "diagnostics/trace_target.py drifted from infer_target")


if __name__ == "__main__":
    unittest.main()

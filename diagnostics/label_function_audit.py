"""Formalizes this project's recurring "check a candidate trigger before
trusting it" pattern into a reusable summary, borrowing the Coverage /
Overlaps / Conflicts / Empirical-Accuracy vocabulary from Snorkel-style weak
supervision label-function auditing.

This is NOT weak supervision. There is no generative model fit, no learned
per-function weights, no probabilistic labels, no discriminative classifier -
none of that is implementable under this project's stdlib-only, no-ML-library
constraint, and none of it is needed here: dev_answers.json already gives us
120 real ground-truth labels, so the useful part of the Snorkel workflow is
just the diagnostic half - deciding whether a candidate rule is worth trusting
before wiring it into harness.py.

Every "고도화 제안" verified so far this session followed the same manual
recipe: count how many dev tasks a candidate phrase/predicate fires on, check
how many of those actually have the implied answer, and count screening
recurrence separately (screening has no ground truth, so it only ever
contributes a coverage number, not an accuracy number). This module runs that
recipe in one call instead of a fresh throwaway script every time.

What this tool does NOT replace: a predicate passing this diagnostic (high
coverage, high empirical accuracy, healthy screening recurrence) is a
necessary but not sufficient reason to adopt it. Before changing harness.py,
still do the full session-threaded before/after comparison this project
requires (see CLAUDE.md, principle 4) - a standalone predicate check here
can't see cascade effects through session state that a real implementation
change can trigger.

Usage as a library:
    from diagnostics.label_function_audit import audit_label_function
    result = audit_label_function(
        predicate=lambda view: "우선한다" in view.all_text,
        field="control",
        implied_value="hold",
        dev_tasks=dev_tasks, dev_answers=dev_answers, screening_tasks=screening_tasks,
    )
    print(result.summary())

CLI self-test (reproduces two already-verified findings from this session as a
sanity check that the tool itself is correct):
    python3 -m diagnostics.label_function_audit --tasks dev_tasks.jsonl --answers dev_answers.json --screening screening_tasks.jsonl
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Callable

from harness import TaskView, load_jsonl


@dataclass
class LabelFunctionAuditResult:
    field: str
    implied_value: str
    dev_coverage: int
    dev_total: int
    dev_correct: int
    dev_conflicts: list[tuple[str, Any]]  # (task_id, actual_value) where predicate fired but answer differs
    screening_coverage: int
    screening_total: int

    @property
    def dev_coverage_rate(self) -> float:
        return self.dev_coverage / self.dev_total if self.dev_total else 0.0

    @property
    def empirical_accuracy(self) -> float | None:
        if self.dev_coverage == 0:
            return None
        return self.dev_correct / self.dev_coverage

    @property
    def screening_coverage_rate(self) -> float:
        return self.screening_coverage / self.screening_total if self.screening_total else 0.0

    def summary(self) -> str:
        acc = self.empirical_accuracy
        acc_str = f"{acc:.0%}" if acc is not None else "n/a (0 dev coverage)"
        lines = [
            f"candidate implies {self.field}={self.implied_value!r}",
            f"  dev coverage:        {self.dev_coverage}/{self.dev_total} ({self.dev_coverage_rate:.1%})",
            f"  dev empirical acc.:  {acc_str} ({self.dev_correct}/{self.dev_coverage} correct)"
            if self.dev_coverage
            else "  dev empirical acc.:  n/a",
            f"  dev conflicts:       {len(self.dev_conflicts)}",
            f"  screening coverage:  {self.screening_coverage}/{self.screening_total} ({self.screening_coverage_rate:.1%})",
        ]
        if self.dev_coverage and self.screening_coverage == 0:
            lines.append("  ⚠ dev-only pattern (0 screening recurrence) - the known overfitting-risk signature, see CLAUDE.md")
        if self.dev_conflicts:
            shown = self.dev_conflicts[:5]
            lines.append(f"  conflict examples: {shown}")
        return "\n".join(lines)


def audit_label_function(
    predicate: Callable[[TaskView], bool],
    field: str,
    implied_value: str,
    dev_tasks: list[dict[str, Any]],
    dev_answers: dict[str, Any],
    screening_tasks: list[dict[str, Any]],
) -> LabelFunctionAuditResult:
    dev_coverage = 0
    dev_correct = 0
    conflicts: list[tuple[str, Any]] = []
    for task in dev_tasks:
        view = TaskView(task)
        if not predicate(view):
            continue
        dev_coverage += 1
        answer = dev_answers.get(str(task.get("id", "")), {})
        actual = answer.get(field)
        if actual == implied_value:
            dev_correct += 1
        else:
            conflicts.append((str(task.get("id", "")), actual))

    screening_coverage = 0
    for task in screening_tasks:
        view = TaskView(task)
        if predicate(view):
            screening_coverage += 1

    return LabelFunctionAuditResult(
        field=field,
        implied_value=implied_value,
        dev_coverage=dev_coverage,
        dev_total=len(dev_tasks),
        dev_correct=dev_correct,
        dev_conflicts=conflicts,
        screening_coverage=screening_coverage,
        screening_total=len(screening_tasks),
    )


def _self_test(dev_tasks: list[dict[str, Any]], dev_answers: dict[str, Any], screening_tasks: list[dict[str, Any]]) -> None:
    """Reproduces two findings already verified by hand earlier this session,
    as a check that the tool's own numbers are trustworthy before relying on
    it for new candidates."""
    print("=== self-test 1: '우선한다' as a hold trigger (expected: 근거 없음, 이미 반증됨) ===")
    result = audit_label_function(
        predicate=lambda view: "우선한다" in view.all_text,
        field="control",
        implied_value="hold",
        dev_tasks=dev_tasks, dev_answers=dev_answers, screening_tasks=screening_tasks,
    )
    print(result.summary())

    print("\n=== self-test 2: '보류 후보로 남겼고' as a hold trigger (expected: dev 14건, 3/14만 정답 hold) ===")
    result = audit_label_function(
        predicate=lambda view: "보류 후보로 남겼고" in view.all_text,
        field="control",
        implied_value="hold",
        dev_tasks=dev_tasks, dev_answers=dev_answers, screening_tasks=screening_tasks,
    )
    print(result.summary())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Self-test the label-function audit tool against known findings.")
    parser.add_argument("--tasks", required=True, help="Path to dev_tasks.jsonl.")
    parser.add_argument("--answers", required=True, help="Path to dev_answers.json.")
    parser.add_argument("--screening", required=True, help="Path to screening_tasks.jsonl.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dev_tasks = load_jsonl(args.tasks)
    dev_answers = json.load(open(args.answers, encoding="utf-8"))["answers"]
    screening_tasks = load_jsonl(args.screening)
    _self_test(dev_tasks, dev_answers, screening_tasks)


if __name__ == "__main__":
    main()

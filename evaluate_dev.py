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

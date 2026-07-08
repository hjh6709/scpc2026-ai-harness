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

# scpc2026-ai-harness

Deterministic Python harness for the SCPC 2026 AI Agent Harness task.

## Rules

- Uses Python standard library only.
- Does not call external APIs, remote models, or network services.
- Exposes `FinalHarness.answer_task(task, session)` for organizer verification.
- Uses `FixedSLMClient.summarize_task()` only as local evidence; final answer fields are produced by harness rules.
- Does not hardcode task ids, session ids, screening rows, or answer maps.

## Generate Submission

```bash
python3 generate_submission.py \
  --tasks "/Users/hanjeonghyun/Downloads/data 3/screening_tasks.jsonl" \
  --output submission.csv
```

The output file is UTF-8 CSV with one column, `submission`, and one data row containing the full answer JSON.

## Validate Submission Locally

The JSON is stored in one large CSV cell, so local Python validation should raise the CSV field size limit first.

```bash
python3 -c 'import csv,json,sys; csv.field_size_limit(sys.maxsize); rows=list(csv.DictReader(open("submission.csv", encoding="utf-8-sig"))); payload=json.loads(rows[0]["submission"]); assert payload["schema"]=="scpc.final.answer.v1"; assert len(payload["answers"])==700; print("valid submission", len(payload["answers"]))'
```

## Dev Diagnostics

```bash
python3 evaluate_dev.py \
  --tasks "/Users/hanjeonghyun/Downloads/data 3/dev_tasks.jsonl" \
  --answers "/Users/hanjeonghyun/Downloads/data 3/dev_answers.json" \
  --show 8
```

## Tests

```bash
python3 -m unittest tests.test_harness -v
```

## Submission Metadata

The generated JSON uses:

- `fixed_slm_policy`: `local_fixed_slm_only`
- `model_id`: `scpc-final-fixed-slm-local-facade`
- `temperature`: `0.0`
- `seed`: `42`

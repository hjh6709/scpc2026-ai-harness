## Summary

- 

## Harness Changes

- 

## Rule Compliance

- [ ] Uses the provided/local `FixedSLMClient` facade only as evidence.
- [ ] Does not call external APIs, remote models, or network services.
- [ ] Does not hardcode task ids, session ids, screening rows, or answer maps.
- [ ] Keeps `FinalHarness.answer_task(task, session)` importable for verification.

## Submission Checks

- [ ] `submission.csv` is UTF-8 CSV with one `submission` column and one data row.
- [ ] Submission JSON uses `schema=scpc.final.answer.v1`.
- [ ] Submission metadata uses `fixed_slm_policy=local_fixed_slm_only`, `model_id=scpc-final-fixed-slm-local-facade`, `temperature=0.0`, and `seed=42`.
- [ ] Screening payload contains exactly 700 answers.

## Test Plan

- [ ] `python3 -m unittest tests.test_harness -v`
- [ ] `python3 evaluate_dev.py --tasks "/Users/hanjeonghyun/Downloads/data 3/dev_tasks.jsonl" --answers "/Users/hanjeonghyun/Downloads/data 3/dev_answers.json" --show 3`
- [ ] `python3 generate_submission.py --tasks "/Users/hanjeonghyun/Downloads/data 3/screening_tasks.jsonl" --output submission.csv`
- [ ] CSV round-trip validation with `csv.field_size_limit(sys.maxsize)`

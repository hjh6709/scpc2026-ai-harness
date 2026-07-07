# SCPC 2026 Agent Harness Design

## Goal

Build a reproducible local harness for the SCPC 2026 AI Agent evaluation. The harness reads task JSONL files, interprets task state, visible history, session memory, and policy signals, then emits answer JSON in `scpc.final.answer.v1` format. It must generate a DACON-compatible `submission.csv` containing one JSON payload in the `submission` column.

The implementation should optimize against the public dev reference without becoming a task-id lookup table or a screening-specific answer cache. The same `harness.py` must be suitable for later organizer verification on unseen task streams.

## Non-Goals

- Do not call external LLM APIs or remote services.
- Do not hardcode answers by task id, screening id, or exact public task row.
- Do not require GPU inference.
- Do not build a notebook-only workflow.

## Inputs and Outputs

Inputs:

- `dev_tasks.jsonl`: public development tasks.
- `dev_answers.json`: public references for local scoring and rule calibration.
- `screening_tasks.jsonl`: public leaderboard tasks.
- `submission_schema.json`: answer JSON structure.

Outputs:

- `submission.csv`: UTF-8 CSV with one column named `submission` and one data row containing the full JSON answer payload.
- Optional local diagnostics for dev scoring and mismatch review.

Answer payload:

```json
{
  "schema": "scpc.final.answer.v1",
  "meta": {
    "harness_name": "scpc_rule_harness",
    "uses_external_api": false,
    "fixed_slm_policy": "local_fixed_slm_only",
    "model_id": "scpc-final-fixed-slm-local-facade",
    "temperature": 0.0,
    "seed": 2026
  },
  "answers": {}
}
```

Each answer contains `focal_id`, `target`, `control`, `content_scope`, `policy`, and `plan_events`, plus optional explanatory fields.

## Architecture

The implementation will use small deterministic modules inside `harness.py`.

1. `TaskView`
   Normalizes access to records, objects, visible history, object attributes, ref codes, and text tokens.

2. `SessionState`
   Stores only information learned while running ordered tasks in the same session, such as last focal id, last target, last control, memory writes, and prior safety or policy outcomes.

3. `FocalResolver`
   Chooses the central object.

4. `TargetResolver`
   Chooses the target channel, person, device, app, memory store, or user confirmation target.

5. `ControlDecider`
   Chooses one of `proceed`, `amend`, `hold`, or `ask`.

6. `ScopeBuilder`
   Produces `content_scope.mode`, allowed fields, excluded fields, and confirmation requirement.

7. `PolicyBuilder`
   Emits canonical risk flags, violations, and confirmation status.

8. `PlanBuilder`
   Emits bounded plan events using public verbs and public ontology buckets.

9. `SubmissionRunner`
   Sorts tasks by `session_id`, `turn_index`, and task id, runs the harness, validates the payload, and writes CSV.

## Focal Resolution

The resolver uses ordered evidence rather than object array position.

Priority:

1. If both `focal_resolution_trace` and `focal_marker_refs` exist, map the latest effective phase to a marker, then marker to `ref_code`, then `ref_code` to object id.
2. If records directly contain an object id, use that object.
3. If visible history names WM/ref candidates and describes a selected or final candidate, prefer the object with that `ref_code`.
4. If records describe ambiguity or candidate comparison, prefer the object matching selected marker, approved channel, or final candidate language.
5. Fallback to prompt/object token overlap, weighted toward body/title/recipient and away from decoy candidates.

This covers dev patterns where identical messages differ only by ref code and where marker indirection is the intended solution path.

## Target Resolution

Target selection is policy-aware.

Rules:

- Local update or memory write boundaries target `memory_store`.
- `hold` and unresolved clarification cases target `user` unless a reference target expects a specific blocked destination for context.
- `resolved_target` wins over visible recipient when the task states the surface recipient may differ from the confirmed channel.
- Verified internal route records can preserve internal channels such as `project_room`, `privacy_review`, or `legal_review`.
- External share policy and object recipient are fallback signals, not primary authority.
- Device, IoT, calendar, health, and payment tasks use object attrs such as `app`, `channel`, `attendee`, `merchant`, or setting name only after policy records are checked.

## Control Decision

The decision layer applies strongest blocking conditions first.

`hold`:

- Precondition invalidated or prior success invalidated.
- Security alert, phishing, impersonation, or safety mode block.
- Consent revoked or policy says raw/sensitive share is forbidden.
- Payment or health request violates explicit safety or verification requirements.

`ask`:

- Ambiguous target or focal without a record that resolves it.
- Duration, amount, merchant, routine scope, memory conflict, or target conflict requires confirmation.
- Prompt explicitly says to confirm who or what scope before acting.

`amend`:

- Action can proceed only with redaction, summary-only disclosure, minimal disclosure, status-only health sharing, or external redacted summary.
- Sensitive fields are present but policy permits a reduced scope.

`proceed`:

- Local update, memory write, verified route, safe routine, or ordinary dispatch is permitted with the selected scope.

## Content Scope

Scope mode is derived from control, policy records, object `contains`, and prompt constraints.

- `none`: hold or no share/update should occur.
- `status_only`: local update, health status-only sharing, or memory state update.
- `redacted`: sensitive content can be shared only after excluding identifiers, raw quotes, locations, numeric values, doctor notes, card numbers, or names.
- `summary`: non-sensitive summarized disclosure.
- `raw`: only when records and prompt clearly permit raw content and no sensitive exclusion applies.

Allowed and excluded fields will use stable canonical labels seen in public references: `summary`, `status`, `title`, `raw_quote`, `rrn`, `name`, `location`, `numeric_value`, `doctor_note`, and related object fields.

## Policy Signals

The policy builder emits reference-style canonical flags instead of free-form tags.

Core flags:

- `strict_share_policy`
- `external_share`
- `local_only`
- `sensitive_content`
- `target_ambiguity`
- `ambiguous_focal`
- `minimal_disclosure`
- `clarification_required`
- `precondition_changed`
- `precondition_invalidated`
- `safety`

Violations are conservative. The main expected violation in public dev is `precondition_changed_ignored`; consent and security violations are used only when records clearly support them and scoring compatibility is better than leaving them empty.

`requires_confirmation` is true when the chosen control is `ask` or when the policy reference indicates confirmation is still needed despite a local action.

## Plan Events

Plan events are concise and use public verbs:

- Every plan starts with `read` on `focal_id`.
- `hold` adds `guard`.
- `ask` adds `clarify`.
- `amend` generally adds `redact` then `dispatch`.
- `proceed` local update adds `verify` then `update`.
- `proceed` dispatch adds `verify` or `dispatch` depending on route certainty.

Arguments use public ontology buckets such as `inspect_context`, `local_update`, `local_status_only`, `minimal_disclosure`, `sensitive_fields`, `redacted`, `summary`, `status_only`, `clarification_required`, `target_ambiguity`, `ambiguous_focal`, `precondition_invalidated`, `invalidated_precondition`, and `route_verified`.

## Files

- `harness.py`
  Deterministic harness implementation.

- `generate_submission.py`
  CLI for producing `submission.csv` from a JSONL task file.

- `evaluate_dev.py`
  Local dev evaluator and mismatch reporter using public references.

- `README.md`
  Setup, commands, data placement, and design notes.

## Testing and Verification

Verification commands:

1. Run the harness on `dev_tasks.jsonl`.
2. Validate answer structure against `submission_schema.json`.
3. Compare local dev outputs with `dev_answers.json` using the public notebook scoring logic where available.
4. Generate `submission.csv` for `screening_tasks.jsonl`.
5. Re-read the CSV cell, parse JSON, and confirm all 700 screening task ids are present exactly once.

Success criteria:

- No external network/API use.
- `submission.csv` has exactly one `submission` column and one row.
- Dev score improves materially over the baseline notebook.
- No task-id answer map or screening-specific lookup table exists in code.
- Harness remains deterministic across repeated runs.

## Risks and Mitigations

- Risk: Overfitting to public dev patterns.
  Mitigation: Encode general record/object policies, not task ids or exact strings.

- Risk: Marker/focal resolution errors dominate score.
  Mitigation: Implement trace-based resolution first and add diagnostics for unresolved or low-confidence focal choices.

- Risk: Plan event arguments lose points through non-canonical labels.
  Mitigation: Centralize canonical plan arg values and aliases in one table.

- Risk: Public scoring helper in notebook differs from server scoring.
  Mitigation: Treat local score as a calibration signal, and prioritize schema validity plus semantically stable answer fields.

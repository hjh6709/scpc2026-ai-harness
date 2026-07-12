import unittest

from evaluate_dev import exact_field_diagnostics, score_dev_submission


def answer(
    focal_id="obj_a",
    target="user",
    control="proceed",
    scope=None,
    policy=None,
    events=None,
):
    return {
        "focal_id": focal_id,
        "target": target,
        "control": control,
        "content_scope": scope
        or {
            "mode": "summary",
            "allowed_fields": ["summary"],
            "excluded_fields": [],
            "requires_user_confirmation": False,
        },
        "policy": policy
        or {
            "risk_flags": ["strict_share_policy"],
            "violations": [],
            "requires_confirmation": False,
        },
        "plan_events": events
        or [
            {"verb": "read", "target": focal_id, "args": {"purpose": "inspect_task_context"}},
            {"verb": "dispatch", "target": target, "args": {"scope": "summary"}},
        ],
        "user_response": f"I will proceed to {target}.",
        "audit_tags": [],
        "counterfactual": "If target, consent, security, or share-boundary records change, this decision may change.",
    }


class DevScoringTests(unittest.TestCase):
    def test_exact_match_scores_all_weighted_axes_except_semantic(self):
        pred = answer()
        ref = dict(pred)
        ref["expected_events"] = pred["plan_events"]
        payload = {
            "schema": "scpc.final.answer.v1",
            "meta": {
                "harness_name": "unit",
                "uses_external_api": False,
                "fixed_slm_policy": "local_fixed_slm_only",
                "model_id": "scpc-final-fixed-slm-local-facade",
                "temperature": 0.0,
                "seed": 42,
            },
            "answers": {"task_1": pred},
        }
        report = score_dev_submission(payload, {"answers": {"task_1": ref}})
        self.assertEqual(report["overall"], 0.96)
        self.assertEqual(report["strict_exact_overall"], 0.96)
        self.assertEqual(report["axes"]["focal"], 1.0)
        self.assertEqual(report["axes"]["plan"], 1.0)
        self.assertEqual(report["strict_exact_axes"]["plan"], 1.0)
        self.assertEqual(exact_field_diagnostics(pred, ref)["canonical_answer_exact"], 0.0)

        minimal_pred = {
            key: pred[key]
            for key in ["focal_id", "target", "control", "content_scope", "policy", "plan_events"]
        }
        self.assertEqual(exact_field_diagnostics(minimal_pred, ref)["canonical_answer_exact"], 1.0)

    def test_scope_policy_and_plan_are_gated_by_target_and_control(self):
        pred = answer(target="wrong", control="proceed")
        ref = answer(target="user", control="proceed")
        ref["expected_events"] = ref["plan_events"]
        payload = {
            "schema": "scpc.final.answer.v1",
            "meta": {
                "harness_name": "unit",
                "uses_external_api": False,
                "fixed_slm_policy": "local_fixed_slm_only",
                "model_id": "scpc-final-fixed-slm-local-facade",
                "temperature": 0.0,
                "seed": 42,
            },
            "answers": {"task_1": pred},
        }
        report = score_dev_submission(payload, {"answers": {"task_1": ref}})
        self.assertEqual(report["axes"]["focal"], 1.0)
        self.assertEqual(report["axes"]["target"], 0.0)
        self.assertEqual(report["axes"]["control"], 1.0)
        self.assertEqual(report["axes"]["content_scope"], 0.0)
        self.assertEqual(report["axes"]["policy"], 0.0)
        self.assertEqual(report["axes"]["plan"], 0.0)

    def test_plan_arg_aliases_are_canonicalized(self):
        pred = answer(
            events=[
                {"verb": "read", "target": "obj_a", "args": {"purpose": "inspect_task_context"}},
                {"verb": "dispatch", "target": "user", "args": {"scope": "summary"}},
            ]
        )
        ref = answer(
            events=[
                {"verb": "read", "target": "obj_a", "args": {"purpose": "inspect_context"}},
                {"verb": "dispatch", "target": "user", "args": {"scope": "summary"}},
            ]
        )
        ref["expected_events"] = ref["plan_events"]
        payload = {
            "schema": "scpc.final.answer.v1",
            "meta": {
                "harness_name": "unit",
                "uses_external_api": False,
                "fixed_slm_policy": "local_fixed_slm_only",
                "model_id": "scpc-final-fixed-slm-local-facade",
                "temperature": 0.0,
                "seed": 42,
            },
            "answers": {"task_1": pred},
        }
        report = score_dev_submission(payload, {"answers": {"task_1": ref}})
        self.assertEqual(report["axes"]["plan"], 1.0)


if __name__ == "__main__":
    unittest.main()

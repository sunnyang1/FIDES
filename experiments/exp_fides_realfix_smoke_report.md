# FIDES-Eval RealFix local harness smoke

**Status:** passed

This validates patch application and test-result normalization in temporary Git clones. It is **not** an official SWE-bench evaluation or capability score.

| Control | Outcome | Required behavior |
|---|---|---|
| `base_reproduces_bug` | PASS | Unchanged base fails its bug-revealing tests |
| `empty_patch_rejected` | PASS | No-op submission is rejected |
| `reference_fix_applies_and_passes` | PASS | Valid source-only fix applies and passes |
| `wrong_fix_applies_but_fails` | PASS | Behavioral regression remains a test failure |
| `test_tampering_rejected_before_apply` | PASS | Protected tests cannot be edited |
| `invalid_patch_rejected` | PASS | Malformed input is rejected |
| `candidate_timeout_normalized` | PASS | Candidate test timeout has a bounded result |
| `baseline_timeout_normalized` | PASS | Base test timeout has a bounded result |
| `candidate_runner_error_normalized` | PASS | Missing candidate runner returns error |
| `baseline_runner_error_normalized` | PASS | Missing base runner returns error |
| `source_repository_unchanged` | PASS | Evaluation never mutates the source checkout |

## Evidence boundary

A local temporary-clone harness validation only. This is not an official SWE-bench result, not a container security boundary, and not evidence of autonomous issue resolution.

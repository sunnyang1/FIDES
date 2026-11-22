"""FIDES-Eval RealFix — deterministic selection and real-Git harness smoke test.

Select 30 tasks from a local SWE-bench Verified JSONL export:
  python3 experiments/exp_fides_realfix.py select --input verified.jsonl

Run the local harness validation (not a SWE-bench score):
  python3 experiments/exp_fides_realfix.py smoke
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.realfix import (
    evaluate_patch,
    git_available,
    load_tasks_jsonl,
    run_test_baseline,
    write_selection_manifest,
)

OUT = Path(__file__).resolve().parent / "exp_fides_realfix_smoke.json"
REPORT = Path(__file__).resolve().parent / "exp_fides_realfix_smoke_report.md"
MANIFEST = Path(__file__).resolve().parent / "realfix_30_manifest.json"
TEST_COMMAND = (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")
SLOW_TEST_COMMAND = (sys.executable, "-c", "import time; time.sleep(5)")

REFERENCE_PATCH = """diff --git a/app/calc.py b/app/calc.py
--- a/app/calc.py
+++ b/app/calc.py
@@ -1,2 +1,2 @@
 def subtract(left, right):
-    return left + right
+    return left - right
"""

WRONG_PATCH = """diff --git a/app/calc.py b/app/calc.py
--- a/app/calc.py
+++ b/app/calc.py
@@ -1,2 +1,2 @@
 def subtract(left, right):
-    return left + right
+    return left - right - 1
"""

PROTECTED_TEST_PATCH = """diff --git a/tests/test_calc.py b/tests/test_calc.py
--- a/tests/test_calc.py
+++ b/tests/test_calc.py
@@ -6,5 +6,5 @@ from app.calc import subtract
 class SubtractTests(unittest.TestCase):
     def test_positive_result(self):
-        self.assertEqual(subtract(5, 2), 3)
+        self.assertEqual(subtract(5, 2), 7)
\x20
     def test_negative_result(self):
"""


def _git(repository: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=repository,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _create_smoke_repository(root: Path) -> tuple[Path, str]:
    repository = root / "source"
    (repository / "app").mkdir(parents=True)
    (repository / "tests").mkdir()
    (repository / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repository / "app" / "calc.py").write_text(
        "def subtract(left, right):\n    return left + right\n", encoding="utf-8"
    )
    (repository / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repository / "tests" / "test_calc.py").write_text(
        "import unittest\n\n"
        "from app.calc import subtract\n\n\n"
        "class SubtractTests(unittest.TestCase):\n"
        "    def test_positive_result(self):\n"
        "        self.assertEqual(subtract(5, 2), 3)\n\n"
        "    def test_negative_result(self):\n"
        "        self.assertEqual(subtract(2, 5), -3)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "RealFix Smoke")
    _git(repository, "config", "user.email", "realfix-smoke@localhost")
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "Reproduce subtraction bug")
    return repository, _git(repository, "rev-parse", "HEAD")


def run_realfix_smoke() -> dict[str, Any]:
    """Exercise base, empty, valid, wrong, and test-tampering patch controls."""
    if not git_available():
        return {
            "suite": "FIDES-Eval RealFix local harness smoke v0.1",
            "status": "error",
            "error": "Git is required for the local real-repository smoke test",
        }

    with tempfile.TemporaryDirectory(prefix="realfix-smoke-") as temp:
        repository, base_commit = _create_smoke_repository(Path(temp))
        baseline = run_test_baseline(repository, TEST_COMMAND, base_commit)
        cases = {
            "empty_patch": evaluate_patch(repository, "", TEST_COMMAND, base_commit),
            "reference_fix": evaluate_patch(repository, REFERENCE_PATCH, TEST_COMMAND, base_commit),
            "wrong_fix": evaluate_patch(repository, WRONG_PATCH, TEST_COMMAND, base_commit),
            "protected_test_tampering": evaluate_patch(
                repository, PROTECTED_TEST_PATCH, TEST_COMMAND, base_commit
            ),
            "invalid_patch": evaluate_patch(
                repository,
                "diff --git a/app/calc.py b/app/calc.py\nnot a patch\n",
                TEST_COMMAND,
                base_commit,
            ),
            "candidate_timeout": evaluate_patch(
                repository,
                REFERENCE_PATCH,
                SLOW_TEST_COMMAND,
                base_commit,
                timeout_seconds=0.05,
            ),
            "baseline_timeout": run_test_baseline(
                repository, SLOW_TEST_COMMAND, base_commit, timeout_seconds=0.05
            ),
            "candidate_runner_error": evaluate_patch(
                repository,
                REFERENCE_PATCH,
                (str(Path(temp) / "missing-test-runner"),),
                base_commit,
            ),
            "baseline_runner_error": run_test_baseline(
                repository, (str(Path(temp) / "missing-test-runner"),), base_commit
            ),
        }
        source_unchanged = not _git(repository, "status", "--porcelain")
        checks = {
            "base_reproduces_bug": baseline["status"] == "failed",
            "empty_patch_rejected": cases["empty_patch"]["status"] == "rejected",
            "reference_fix_applies_and_passes": cases["reference_fix"]["status"] == "passed"
            and cases["reference_fix"]["patch_applied"],
            "wrong_fix_applies_but_fails": cases["wrong_fix"]["status"] == "failed"
            and cases["wrong_fix"]["patch_applied"],
            "test_tampering_rejected_before_apply": cases["protected_test_tampering"]["status"]
            == "protected_path"
            and not cases["protected_test_tampering"]["patch_applied"],
            "invalid_patch_rejected": cases["invalid_patch"]["status"] == "invalid_patch",
            "candidate_timeout_normalized": cases["candidate_timeout"]["status"] == "timeout"
            and cases["candidate_timeout"]["returncode"] == 124,
            "baseline_timeout_normalized": cases["baseline_timeout"]["status"] == "timeout",
            "candidate_runner_error_normalized": cases["candidate_runner_error"]["status"]
            == "error",
            "baseline_runner_error_normalized": cases["baseline_runner_error"]["status"] == "error",
            "source_repository_unchanged": source_unchanged,
        }
        return {
            "suite": "FIDES-Eval RealFix local harness smoke v0.1",
            "status": "passed" if all(checks.values()) else "failed",
            "base_commit": base_commit,
            "test_command": list(TEST_COMMAND),
            "baseline": baseline,
            "cases": cases,
            "checks": checks,
            "metrics": {
                "controls": len(cases),
                "unsafe_test_tampering_applied": int(
                    cases["protected_test_tampering"]["patch_applied"]
                ),
            },
            "boundary": (
                "A local temporary-clone harness validation only. This is not an official "
                "SWE-bench result, not a container security boundary, and not evidence of "
                "autonomous issue resolution."
            ),
        }


def _write_smoke_outputs(result: dict[str, Any], output: Path, report: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# FIDES-Eval RealFix local harness smoke",
        "",
        f"**Status:** {result['status']}",
        "",
        "This validates patch application and test-result normalization in temporary Git "
        "clones. It is **not** an official SWE-bench evaluation or capability score.",
        "",
        "| Control | Outcome | Required behavior |",
        "|---|---|---|",
    ]
    descriptions = {
        "base_reproduces_bug": "Unchanged base fails its bug-revealing tests",
        "empty_patch_rejected": "No-op submission is rejected",
        "reference_fix_applies_and_passes": "Valid source-only fix applies and passes",
        "wrong_fix_applies_but_fails": "Behavioral regression remains a test failure",
        "test_tampering_rejected_before_apply": "Protected tests cannot be edited",
        "invalid_patch_rejected": "Malformed input is rejected",
        "candidate_timeout_normalized": "Candidate test timeout has a bounded result",
        "baseline_timeout_normalized": "Base test timeout has a bounded result",
        "candidate_runner_error_normalized": "Missing candidate runner returns error",
        "baseline_runner_error_normalized": "Missing base runner returns error",
        "source_repository_unchanged": "Evaluation never mutates the source checkout",
    }
    for check, passed in result.get("checks", {}).items():
        lines.append(f"| `{check}` | {'PASS' if passed else 'FAIL'} | {descriptions[check]} |")
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            result.get("boundary", result.get("error", "No result was produced.")),
            "",
        ]
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    select = subparsers.add_parser("select", help="select RealFix-30 from local JSONL")
    select.add_argument("--input", required=True, help="local SWE-bench Verified JSONL export")
    select.add_argument("--output", type=Path, default=MANIFEST)
    smoke = subparsers.add_parser("smoke", help="run local real-Git harness controls")
    smoke.add_argument("--output", type=Path, default=OUT)
    smoke.add_argument("--report", type=Path, default=REPORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "smoke"
    if command == "select":
        manifest = write_selection_manifest(load_tasks_jsonl(args.input), args.input, args.output)
        print(
            f"RealFix-30: selected {manifest['selection']['size']} tasks from "
            f"{manifest['selection']['repository_count']} repositories; evaluation not run"
        )
        return 0

    result = run_realfix_smoke()
    output = args.output if args.command else OUT
    report = args.report if args.command else REPORT
    _write_smoke_outputs(result, output, report)
    print(f"RealFix local smoke: {result['status'].upper()} (not a SWE-bench score)")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

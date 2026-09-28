"""Acceptance tests for the reusable deterministic QA harness."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from hermes_cli.qa_evidence_artifact import load_verified_qa_evidence
from hermes_cli.qa_harness import HARNESS_STANDARD, run_plan


WORKER = '''
import json, os, sys
from pathlib import Path
passed = sys.argv[1] == "pass"
receipt = {
  "passed": passed,
  "execution_route": {
    "initial_provider": "test", "initial_model": "fixture",
    "actual_provider": "test", "actual_model": "fixture",
    "transitions": [],
    "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0,
              "context_window_tokens": 100, "cost_status": "known", "cost_usd": 0.0}
  },
  "tool_outcomes": [{"tool": "fixture", "ok": passed}]
}
Path(os.environ["HERMES_QA_RUN_RECEIPT"]).write_text(json.dumps(receipt))
raise SystemExit(0 if passed else 1)
'''


def _plan(worker: Path, outcomes=("pass",)):
    return {
        "task_id": "t_test",
        "run_id": 7,
        "candidate_sha": "b" * 40,
        "timeout_seconds": 5,
        "max_consecutive_failures": 2,
        "configured_route": [{"provider": "test", "model": "fixture"}],
        "environment_before": {"production": False},
        "environment_after": {"production": False},
        "scenarios": [
            {"id": f"scenario-{index}", "argv": [sys.executable, str(worker), outcome]}
            for index, outcome in enumerate(outcomes, start=1)
        ],
    }


def test_harness_runs_matrix_and_writes_verified_artifact(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER)
    result = run_plan(_plan(worker, ("pass", "pass")), output_dir=tmp_path / "out")
    assert result["terminal_state"] == "complete"
    evidence, error = load_verified_qa_evidence(
        result["evidence_artifact"], task_id="t_test", run_id=7,
    )
    assert error is None
    assert evidence is not None
    assert evidence["harness_standard"] == HARNESS_STANDARD
    assert evidence["completed_run_count"] == 2
    assert evidence["passed_run_count"] == 2
    assert all(run["stdout_sha256"] and run["stderr_sha256"] for run in evidence["live_runs"])


def test_harness_opens_circuit_after_bounded_failures(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER)
    result = run_plan(_plan(worker, ("fail", "fail", "pass")), output_dir=tmp_path / "out")
    assert result["terminal_state"] == "circuit_open"
    evidence, error = load_verified_qa_evidence(
        result["evidence_artifact"], task_id="t_test", run_id=7,
    )
    assert error is None
    assert evidence is not None
    assert evidence["completed_run_count"] == 2
    assert evidence["expected_run_count"] == 3


def test_harness_rejects_shell_string_argv(tmp_path):
    plan = {
        "task_id": "t_test", "run_id": None, "candidate_sha": "b" * 40,
        "configured_route": [{"provider": "test", "model": "fixture"}],
        "scenarios": [{"id": "unsafe", "argv": "echo unsafe"}],
    }
    try:
        run_plan(plan, output_dir=tmp_path / "out")
    except ValueError as exc:
        assert "argv must be a non-empty string list" in str(exc)
    else:
        raise AssertionError("string shell command was accepted")

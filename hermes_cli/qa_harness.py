"""Reusable deterministic command harness for governed MaaV QA campaigns."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from hermes_cli.qa_evidence_artifact import write_qa_evidence_artifact

HARNESS_STANDARD = "maav.deterministic-qa.v1"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _validate_plan(plan: dict[str, Any]) -> None:
    required = ("task_id", "run_id", "candidate_sha", "configured_route", "scenarios")
    missing = [field for field in required if field not in plan]
    if missing:
        raise ValueError(f"plan missing {missing}")
    if not isinstance(plan["task_id"], str) or not plan["task_id"].strip():
        raise ValueError("task_id must be a non-empty string")
    if plan["run_id"] is not None and not isinstance(plan["run_id"], int):
        raise ValueError("run_id must be an integer or null")
    if not isinstance(plan["candidate_sha"], str) or len(plan["candidate_sha"]) != 40:
        raise ValueError("candidate_sha must be a full 40-character Git SHA")
    if not isinstance(plan["configured_route"], list) or not plan["configured_route"]:
        raise ValueError("configured_route must be a non-empty list")
    if not isinstance(plan["scenarios"], list) or not plan["scenarios"]:
        raise ValueError("scenarios must be a non-empty list")
    seen: set[str] = set()
    for scenario in plan["scenarios"]:
        if not isinstance(scenario, dict):
            raise ValueError("each scenario must be an object")
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or not scenario_id.strip() or scenario_id in seen:
            raise ValueError("scenario ids must be non-empty and unique")
        seen.add(scenario_id)
        argv = scenario.get("argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError(f"scenario {scenario_id} argv must be a non-empty string list")
        repetitions = scenario.get("repetitions", 1)
        if not isinstance(repetitions, int) or repetitions < 1:
            raise ValueError(f"scenario {scenario_id} repetitions must be a positive integer")
    timeout = plan.get("timeout_seconds", 120)
    if not isinstance(timeout, int) or timeout < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    threshold = plan.get("max_consecutive_failures", 3)
    if not isinstance(threshold, int) or threshold < 1:
        raise ValueError("max_consecutive_failures must be a positive integer")


def run_plan(plan: dict[str, Any], *, output_dir: str | Path) -> dict[str, Any]:
    """Execute a plan without a shell and write one tamper-evident campaign artifact."""
    _validate_plan(plan)
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    timeout = plan.get("timeout_seconds", 120)
    threshold = plan.get("max_consecutive_failures", 3)
    runs: list[dict[str, Any]] = []
    consecutive_failures = 0
    circuit_open = False

    for scenario in plan["scenarios"]:
        if circuit_open:
            break
        for repetition in range(1, scenario.get("repetitions", 1) + 1):
            receipt_path = root / f"{scenario['id']}-{repetition}-receipt.json"
            env = os.environ.copy()
            env.update({str(k): str(v) for k, v in plan.get("environment", {}).items()})
            env["HERMES_QA_RUN_RECEIPT"] = str(receipt_path)
            started = time.monotonic()
            timed_out = False
            stdout = ""
            stderr = ""
            exit_code: int | None = None
            try:
                result = subprocess.run(
                    scenario["argv"], cwd=scenario.get("cwd"), env=env,
                    text=True, capture_output=True, timeout=timeout, check=False,
                )
                exit_code, stdout, stderr = result.returncode, result.stdout, result.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            latency = round(time.monotonic() - started, 6)
            receipt: dict[str, Any] = {}
            receipt_error = None
            try:
                receipt = _load_json(receipt_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                receipt_error = str(exc)
            passed = bool(not timed_out and exit_code == 0 and receipt_error is None and receipt.get("passed") is True)
            run = {
                "scenario_id": scenario["id"],
                "repetition": repetition,
                "role": scenario.get("role"),
                "argv": scenario["argv"],
                "latency_seconds": latency,
                "timed_out": timed_out,
                "exit_code": exit_code,
                "stdout_sha256": _digest(stdout),
                "stderr_sha256": _digest(stderr),
                "receipt_path": str(receipt_path),
                "receipt_error": receipt_error,
                "passed": passed,
                **receipt,
            }
            runs.append(run)
            consecutive_failures = 0 if passed else consecutive_failures + 1
            if consecutive_failures >= threshold:
                circuit_open = True
                break

    expected_runs = sum(item.get("repetitions", 1) for item in plan["scenarios"])
    evidence = {
        "harness_standard": HARNESS_STANDARD,
        "timeout_seconds": timeout,
        "max_consecutive_failures": threshold,
        "configured_route": plan["configured_route"],
        "scenario_matrix": [
            {"id": item["id"], "repetitions": item.get("repetitions", 1), "role": item.get("role")}
            for item in plan["scenarios"]
        ],
        "environment_before": plan.get("environment_before", {}),
        "environment_after": plan.get("environment_after", plan.get("environment_before", {})),
        "live_runs": runs,
        "expected_run_count": expected_runs,
        "completed_run_count": len(runs),
        "passed_run_count": sum(int(run["passed"]) for run in runs),
        "terminal_state": "circuit_open" if circuit_open else ("complete" if len(runs) == expected_runs else "incomplete"),
    }
    artifact_path = root / "qa-evidence.json"
    reference = write_qa_evidence_artifact(
        artifact_path, task_id=plan["task_id"], run_id=plan["run_id"],
        candidate_sha=plan["candidate_sha"], evidence=evidence,
    )
    return {"evidence_artifact": reference, "terminal_state": evidence["terminal_state"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic MaaV QA plan")
    parser.add_argument("plan", help="Path to the QA plan JSON")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = run_plan(_load_json(Path(args.plan)), output_dir=args.output_dir)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["terminal_state"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

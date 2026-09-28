"""Structured acceptance for deterministic, artifact-backed live QA gates."""
from __future__ import annotations

from typing import Any

from hermes_cli.qa_evidence_artifact import load_verified_qa_evidence
from hermes_cli.qa_harness import HARNESS_STANDARD

QA_LIVE_EVIDENCE_CONTRACT = "qa-live-evidence"
_ROUTE_FIELDS = ("initial_provider", "initial_model", "actual_provider", "actual_model", "transitions", "usage")
_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "context_window_tokens", "cost_status", "cost_usd")


def _valid_optional_count(value: Any) -> bool:
    return value is None or (isinstance(value, int) and value >= 0)


def _route_error(run: dict, index: int) -> str | None:
    route = run.get("execution_route")
    if not isinstance(route, dict):
        return f"live_runs[{index}].execution_route is required"
    missing = [field for field in _ROUTE_FIELDS if field not in route]
    if missing:
        return f"live_runs[{index}].execution_route missing {missing}"
    for field in ("initial_provider", "initial_model", "actual_provider", "actual_model"):
        if not isinstance(route[field], str) or not route[field].strip():
            return f"live_runs[{index}].execution_route.{field} must be a non-empty string"
    if not isinstance(route["transitions"], list):
        return f"live_runs[{index}].execution_route.transitions must be a list"
    for transition_index, transition in enumerate(route["transitions"], start=1):
        if not isinstance(transition, dict):
            return f"live_runs[{index}].execution_route.transitions[{transition_index}] must be an object"
        required = ("from_provider", "from_model", "to_provider", "to_model", "reason")
        if any(not isinstance(transition.get(field), str) or not transition[field].strip() for field in required):
            return f"live_runs[{index}].execution_route.transitions[{transition_index}] must record from/to provider/model and reason"
    usage = route["usage"]
    if not isinstance(usage, dict) or any(field not in usage for field in _USAGE_FIELDS):
        return f"live_runs[{index}].execution_route.usage must include {_USAGE_FIELDS}"
    if any(not _valid_optional_count(usage[field]) for field in _USAGE_FIELDS[:4]):
        return f"live_runs[{index}].execution_route.usage token/context values must be non-negative integers or null"
    if usage["cost_status"] not in {"known", "unknown"}:
        return f"live_runs[{index}].execution_route.usage.cost_status must be known or unknown"
    if usage["cost_status"] == "unknown" and usage["cost_usd"] is not None:
        return f"live_runs[{index}].execution_route.usage.cost_usd must be null when cost_status is unknown"
    if usage["cost_status"] == "known" and (not isinstance(usage["cost_usd"], (int, float)) or usage["cost_usd"] < 0):
        return f"live_runs[{index}].execution_route.usage.cost_usd must be non-negative when cost_status is known"
    return None


def collect_qa_acceptance(metadata: Any, *, task_id: str, run_id: int | None) -> dict:
    """Validate an immutable harness artifact instead of worker-authored claims."""
    receipt = {
        "ok": False,
        "classification": "missing_evidence",
        "recovery": "Run the deterministic QA harness and supply its verified artifact reference.",
    }
    if not isinstance(metadata, dict):
        receipt["detail"] = "metadata must be an object for qa-live-evidence"
        return receipt
    evidence, artifact_error = load_verified_qa_evidence(
        metadata.get("evidence_artifact"), task_id=task_id, run_id=run_id,
    )
    if artifact_error:
        receipt["detail"] = artifact_error
        return receipt
    assert evidence is not None
    if evidence.get("harness_standard") != HARNESS_STANDARD:
        receipt["detail"] = f"harness_standard must equal {HARNESS_STANDARD}"
        return receipt
    configured_route = evidence.get("configured_route")
    if not isinstance(configured_route, list) or not configured_route:
        receipt["detail"] = "configured_route must contain the ordered configured provider/model route"
        return receipt
    for index, entry in enumerate(configured_route, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("provider"), str) or not entry["provider"].strip() or not isinstance(entry.get("model"), str) or not entry["model"].strip():
            receipt["detail"] = f"configured_route[{index}] must contain non-empty provider and model"
            return receipt
    matrix = evidence.get("scenario_matrix")
    if not isinstance(matrix, list) or not matrix:
        receipt["detail"] = "scenario_matrix must be a non-empty list"
        return receipt
    expected: dict[str, int] = {}
    for item in matrix:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            receipt["detail"] = "every scenario_matrix entry must have a non-empty id"
            return receipt
        repetitions = item.get("repetitions", 1)
        if item["id"] in expected or not isinstance(repetitions, int) or repetitions < 1:
            receipt["detail"] = "scenario ids must be unique and repetitions must be positive integers"
            return receipt
        expected[item["id"]] = repetitions
    runs = evidence.get("live_runs")
    if not isinstance(runs, list):
        receipt["detail"] = "live_runs must be a list"
        return receipt
    observed = {key: 0 for key in expected}
    for index, run in enumerate(runs, start=1):
        if not isinstance(run, dict):
            receipt["detail"] = f"live_runs[{index}] must be an object"
            return receipt
        scenario_id = run.get("scenario_id")
        if scenario_id not in expected:
            receipt["detail"] = f"live_runs[{index}].scenario_id is not declared by scenario_matrix"
            return receipt
        observed[scenario_id] += 1
        if run.get("timed_out") is not False or run.get("exit_code") != 0 or run.get("receipt_error") is not None or run.get("passed") is not True:
            receipt["detail"] = f"live_runs[{index}] did not produce a successful deterministic receipt"
            return receipt
        if not isinstance(run.get("latency_seconds"), (int, float)) or run["latency_seconds"] < 0:
            receipt["detail"] = f"live_runs[{index}].latency_seconds must be non-negative"
            return receipt
        route_error = _route_error(run, index)
        if route_error:
            receipt["detail"] = route_error
            return receipt
    if observed != expected:
        receipt["detail"] = f"scenario coverage mismatch: expected {expected}, observed {observed}"
        return receipt
    expected_count = sum(expected.values())
    if evidence.get("expected_run_count") != expected_count or evidence.get("completed_run_count") != len(runs):
        receipt["detail"] = "run-count aggregates do not match scenario_matrix/live_runs"
        return receipt
    if evidence.get("passed_run_count") != len(runs):
        receipt["detail"] = "passed_run_count must equal the completed run count"
        return receipt
    if evidence.get("terminal_state") != "complete":
        receipt["detail"] = "terminal_state must be complete; circuit-open or incomplete campaigns cannot pass"
        return receipt
    if evidence.get("environment_before") != evidence.get("environment_after"):
        receipt["detail"] = "environment_before and environment_after must match"
        return receipt
    return {
        "ok": True,
        "classification": "success",
        "harness_standard": HARNESS_STANDARD,
        "run_count": len(runs),
        "scenarios": sorted(expected),
        "configured_route": configured_route,
        "artifact_sha256": metadata["evidence_artifact"]["sha256"],
        "candidate_sha": metadata["evidence_artifact"]["candidate_sha"],
    }

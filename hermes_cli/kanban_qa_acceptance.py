"""Structured acceptance for bounded live QA Kanban gates."""
from __future__ import annotations

from typing import Any

QA_LIVE_EVIDENCE_CONTRACT = "qa-live-evidence"
_REQUIRED_ROLES = {"bull", "bear", "chief", "risk", "pm"}
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


def collect_qa_acceptance(metadata: Any) -> dict:
    """Return a machine-readable receipt for a live QA evidence handoff."""
    receipt = {
        "ok": False,
        "classification": "missing_evidence",
        "required_roles": sorted(_REQUIRED_ROLES),
        "recovery": "Supply complete structured live QA evidence and retry completion.",
    }
    if not isinstance(metadata, dict):
        receipt["detail"] = "metadata must be an object for qa-live-evidence"
        return receipt
    runs = metadata.get("live_runs")
    if not isinstance(runs, list) or len(runs) < 10:
        receipt["detail"] = "live_runs must contain at least 10 records"
        return receipt
    if metadata.get("timeout_seconds") != 120:
        receipt["detail"] = "timeout_seconds must equal 120"
        return receipt
    if metadata.get("cron_before_enabled") is not False or metadata.get("cron_after_enabled") is not False:
        receipt["detail"] = "cron_before_enabled and cron_after_enabled must both be false"
        return receipt
    configured_route = metadata.get("configured_route")
    if not isinstance(configured_route, list) or not configured_route:
        receipt["detail"] = "configured_route must contain the ordered configured provider/model route"
        return receipt
    for index, entry in enumerate(configured_route, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("provider"), str) or not entry["provider"].strip() or not isinstance(entry.get("model"), str) or not entry["model"].strip():
            receipt["detail"] = f"configured_route[{index}] must contain non-empty provider and model"
            return receipt
    roles: set[str] = set()
    first_attempt_valid = 0
    final_valid = 0
    for index, run in enumerate(runs, start=1):
        if not isinstance(run, dict):
            receipt["detail"] = f"live_runs[{index}] must be an object"
            return receipt
        role = run.get("role")
        if role not in _REQUIRED_ROLES:
            receipt["detail"] = f"live_runs[{index}].role must be one of {sorted(_REQUIRED_ROLES)}"
            return receipt
        if not isinstance(run.get("attempt1_schema_valid"), bool) or not isinstance(run.get("final_schema_valid"), bool):
            receipt["detail"] = f"live_runs[{index}] must declare boolean attempt1_schema_valid and final_schema_valid"
            return receipt
        if not isinstance(run.get("retry_invoked"), bool):
            receipt["detail"] = f"live_runs[{index}].retry_invoked must be boolean"
            return receipt
        if not isinstance(run.get("latency_seconds"), (int, float)) or run["latency_seconds"] < 0:
            receipt["detail"] = f"live_runs[{index}].latency_seconds must be non-negative"
            return receipt
        route_error = _route_error(run, index)
        if route_error:
            receipt["detail"] = route_error
            return receipt
        roles.add(role)
        first_attempt_valid += int(run["attempt1_schema_valid"])
        final_valid += int(run["final_schema_valid"])
    if roles != _REQUIRED_ROLES:
        receipt["detail"] = f"live_runs must cover every role; observed {sorted(roles)}"
        return receipt
    if final_valid != len(runs):
        receipt["detail"] = "every live run must be schema-valid after the implementation's bounded retry"
        return receipt
    if metadata.get("first_attempt_schema_valid_count") != first_attempt_valid:
        receipt["detail"] = "first_attempt_schema_valid_count does not match live_runs"
        return receipt
    if metadata.get("final_schema_valid_count") != final_valid:
        receipt["detail"] = "final_schema_valid_count does not match live_runs"
        return receipt
    if not isinstance(metadata.get("evidence_artifact"), str) or not metadata["evidence_artifact"].strip():
        receipt["detail"] = "evidence_artifact is required"
        return receipt
    return {
        "ok": True,
        "classification": "success",
        "run_count": len(runs),
        "first_attempt_schema_valid_count": first_attempt_valid,
        "final_schema_valid_count": final_valid,
        "roles": sorted(roles),
        "configured_route": configured_route,
    }
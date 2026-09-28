"""Tamper-evident artifact helpers for governed MaaV QA runs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "maav.qa-evidence.v1"


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def write_qa_evidence_artifact(path: str | Path, *, task_id: str, run_id: int | None,
                               candidate_sha: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Write a canonical artifact and return the reference used at completion."""
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "identity": {"task_id": task_id, "run_id": run_id, "candidate_sha": candidate_sha},
        "evidence": evidence,
    }
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_bytes(artifact)
    target.write_bytes(payload)
    return {
        "path": str(target),
        "sha256": hashlib.sha256(payload).hexdigest(),
        **artifact["identity"],
    }


def load_verified_qa_evidence(reference: Any, *, task_id: str, run_id: int | None) -> tuple[dict[str, Any] | None, str | None]:
    """Load an artifact only when its digest and task/run identity match."""
    if not isinstance(reference, dict):
        return None, "evidence_artifact must be an object containing path, sha256, task_id, run_id, and candidate_sha"
    required = ("path", "sha256", "task_id", "run_id", "candidate_sha")
    if any(field not in reference for field in required):
        return None, f"evidence_artifact must include {required}"
    if reference["task_id"] != task_id or reference["run_id"] != run_id:
        return None, "evidence_artifact task_id/run_id does not match the completing task run"
    if not isinstance(reference["candidate_sha"], str) or len(reference["candidate_sha"]) != 40:
        return None, "evidence_artifact candidate_sha must be a full 40-character Git SHA"
    try:
        target = Path(reference["path"]).expanduser().resolve(strict=True)
        payload = target.read_bytes()
    except (OSError, RuntimeError) as exc:
        return None, f"evidence_artifact cannot be read: {exc}"
    digest = hashlib.sha256(payload).hexdigest()
    if not isinstance(reference["sha256"], str) or digest != reference["sha256"].lower():
        return None, "evidence_artifact sha256 does not match the artifact bytes"
    try:
        artifact = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"evidence_artifact is not valid UTF-8 JSON: {exc}"
    if artifact.get("schema_version") != SCHEMA_VERSION:
        return None, f"evidence_artifact schema_version must equal {SCHEMA_VERSION}"
    identity = artifact.get("identity")
    expected_identity = {"task_id": task_id, "run_id": run_id, "candidate_sha": reference["candidate_sha"]}
    if identity != expected_identity:
        return None, "evidence_artifact embedded identity does not match its completion reference"
    evidence = artifact.get("evidence")
    if not isinstance(evidence, dict):
        return None, "evidence_artifact evidence must be an object"
    return evidence, None

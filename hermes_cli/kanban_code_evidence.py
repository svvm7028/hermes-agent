"""Server-side code evidence acceptance for Developer kanban cards.

Mirrors the independent standalone verifier at
~/.hermes/profiles/director-products/scripts/kanban_completion_verify.py
which was reviewed and passed on 2026-09-23 (t_87443bfc -> t_528342ed).
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any

CODE_EVIDENCE_CONTRACT = "code-evidence"


def collect_code_evidence(metadata: dict[str, Any], workspace_path: str) -> dict[str, Any]:
    """Return a machine-readable receipt for a code-evidence completion handoff.

    Runs three checks fresh against the workspace git state:
    1. commit_sha resolves to the exact current workspace HEAD
    2. git status --porcelain is empty (clean tree)
    3. Test receipt pattern (N passed/failed/error) present in metadata/summary/comments

    All three must pass for ok=True. Any single failure -> ok=False with
    classification identifying which check(s) failed.
    """
    receipt: dict[str, Any] = {
        "ok": False,
        "classification": "check_failed",
        "checks": [],
        "detail": "",
        "recovery": "Fix the evidence and retry kanban_complete, or kanban_block if you cannot.",
    }

    commit_sha = metadata.get("commit_sha") if isinstance(metadata, dict) else None
    checks = []

    # --- Check 1: commit_sha resolves to a real commit object and is reachable ---
    check1_ok = False
    check1_detail = ""
    if not commit_sha:
        check1_detail = f"no commit_sha in run metadata (metadata={metadata})"
    else:
        # Check it resolves to a commit object
        r = subprocess.run(
            ["git", "-C", workspace_path, "cat-file", "-t", commit_sha],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 or r.stdout.strip() != "commit":
            check1_detail = (
                f"commit_sha '{commit_sha}' does NOT resolve to a commit object in {workspace_path}. "
                f"git cat-file -t {commit_sha} -> rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}"
            )
        else:
            # The receipt must bind to the actual workspace head. An ancestor
            # can be a stale handoff from before unrelated/unreviewed changes.
            head = subprocess.run(
                ["git", "-C", workspace_path, "log", "-1", "--format=%H"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if head == commit_sha:
                check1_ok = True
                check1_detail = f"commit_sha {commit_sha} equals workspace HEAD"
            else:
                check1_detail = (
                    f"commit_sha {commit_sha} exists but does not equal workspace HEAD ({head})"
                )
    checks.append({"name": "commit_sha_resolves", "ok": check1_ok, "detail": check1_detail})

    # --- Check 2: clean tree ---
    check2_ok = False
    check2_detail = ""
    r = subprocess.run(
        ["git", "-C", workspace_path, "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if r.stdout.strip():
        check2_detail = f"working tree is NOT clean:\n{r.stdout}"
    else:
        check2_ok = True
        check2_detail = "git status --porcelain is empty"
    checks.append({"name": "clean_tree", "ok": check2_ok, "detail": check2_detail})

    # --- Check 3: test receipt pattern ---
    check3_ok = False
    check3_detail = ""
    # We'll be given summary and comments from the caller via metadata
    # For now, extract from metadata if present
    summary = metadata.get("summary") if isinstance(metadata, dict) else None
    comments = metadata.get("comments") if isinstance(metadata, dict) else []
    haystacks = [summary, json.dumps(metadata), *(comments if isinstance(comments, list) else [])]
    pattern = re.compile(r"\d+\s+passed|\d+\s+failed|\d+\s+error", re.IGNORECASE)
    found = any(pattern.search(h or "") for h in haystacks)
    if found:
        check3_ok = True
        check3_detail = "found a pytest-style summary pattern (N passed/failed/error) in metadata/comments"
    else:
        check3_detail = (
            "no pytest-style summary pattern found anywhere in metadata/summary/comments -- claims are prose only"
        )
    checks.append({"name": "test_receipt", "ok": check3_ok, "detail": check3_detail})

    # Overall
    all_pass = check1_ok and check2_ok and check3_ok
    receipt["ok"] = all_pass
    receipt["checks"] = checks

    if not all_pass:
        failed = [c["name"] for c in checks if not c["ok"]]
        receipt["classification"] = "_".join(failed)
        receipt["detail"] = "; ".join(c["detail"] for c in checks if not c["ok"])

    return receipt
"""Persist acceptance with the same ownership snapshot as the terminal write."""
from __future__ import annotations

import time

from hermes_cli.kanban_db_connect import write_txn
from hermes_cli.kanban_pr_acceptance import _PR, collect_acceptance
from hermes_cli.kanban_code_evidence import CODE_EVIDENCE_CONTRACT, collect_code_evidence
from hermes_cli.kanban_qa_acceptance import QA_LIVE_EVIDENCE_CONTRACT, collect_qa_acceptance


def _snapshot(conn, task_id):
    row = conn.execute(
        "SELECT current_run_id, status, completion_contract, workspace_kind, workspace_path, assignee FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    return tuple(row) if row else None


def _has_live_bootstrap_grant(conn, task_id, now):
    """A grant is an explicit, bounded bootstrap authorization, never a bypass flag."""
    return conn.execute(
        """
        SELECT 1 FROM kanban_bootstrap_grants
         WHERE task_id = ?
           AND consumed_at IS NULL
           AND expires_at >= ?
        """,
        (task_id, now),
    ).fetchone() is not None


def prepare_acceptance(conn, task_id, expected_run_id, metadata):
    snapshot = _snapshot(conn, task_id)
    if snapshot is None:
        return False
    run_id, status, contract, workspace_kind, workspace_path, assignee = snapshot
    if not contract or contract == "local-only":
        # A Developer task with a real code workspace must declare its
        # code-evidence contract before work starts. The only exception is a
        # live, administrator-created bootstrap grant; it is consumed under
        # the terminal write lock in record_acceptance below.
        if (
            assignee == "developer"
            and workspace_kind in ("dir", "worktree")
            and contract != CODE_EVIDENCE_CONTRACT
        ):
            if _has_live_bootstrap_grant(conn, task_id, int(time.time())):
                return snapshot, {
                    "ok": True,
                    "classification": "bootstrap_grant",
                    "detail": "Completion is authorized by a live, single-use bootstrap grant.",
                    "recovery": "",
                    "bootstrap_grant": True,
                }
            receipt = {
                "ok": False,
                "classification": "missing_code_evidence_contract",
                "detail": f"Task {task_id} looks code-bearing (assignee=developer, workspace_kind={workspace_kind}) but never declared completion_contract=code-evidence (got {contract!r}). This contract must be set at task creation by the router (DOP/EM); the worker in-flight cannot fix it by retrying with different metadata.",
                "recovery": "Declare code-evidence at task creation, or obtain a bounded administrator bootstrap grant for the sole bootstrap task. This is not worker-retryable.",
            }
            return snapshot, receipt
        return None
    if status not in {"running", "ready", "blocked", "review"} or (expected_run_id is not None and run_id != expected_run_id):
        return False
    if contract == QA_LIVE_EVIDENCE_CONTRACT:
        return snapshot, collect_qa_acceptance(
            metadata, task_id=task_id, run_id=run_id,
        )
    if contract == CODE_EVIDENCE_CONTRACT:
        return snapshot, collect_code_evidence(metadata, workspace_path or "")
    published_pr = metadata.get("published_pr") if isinstance(metadata, dict) else None
    match = _PR.fullmatch(published_pr) if isinstance(published_pr, str) else None
    # Publication binds once. Retrying cannot replace the task's PR with a green sibling.
    if match and contract == match[1]:
        with write_txn(conn):
            if _snapshot(conn, task_id) != snapshot:
                return False
            conn.execute("UPDATE tasks SET completion_contract=? WHERE id=?", (published_pr, task_id))
        snapshot = (run_id, status, published_pr, workspace_kind, workspace_path, assignee)
        contract = published_pr
    return snapshot, collect_acceptance(contract, published_pr)


def record_acceptance(conn, task_id, acceptance):
    """Called under complete_task's write_txn, before its terminal UPDATE."""
    from hermes_cli.kanban_db import _append_event

    snapshot, receipt = acceptance
    if _snapshot(conn, task_id) != snapshot:
        return False
    if receipt.get("bootstrap_grant"):
        now = int(time.time())
        consumed = conn.execute(
            """
            UPDATE kanban_bootstrap_grants
               SET consumed_at = ?, consumed_run_id = ?
             WHERE task_id = ?
               AND consumed_at IS NULL
               AND expires_at >= ?
            """,
            (now, snapshot[0], task_id, now),
        )
        if consumed.rowcount != 1:
            return False
    _append_event(conn, task_id, "pr_acceptance", receipt, run_id=snapshot[0])
    if not receipt["ok"]:
        detail = f"PR acceptance {receipt['classification']}: {receipt.get('detail', '')} {receipt['recovery']}"
        conn.execute("UPDATE tasks SET last_failure_error=? WHERE id=?", (detail, task_id))
    return receipt["ok"]

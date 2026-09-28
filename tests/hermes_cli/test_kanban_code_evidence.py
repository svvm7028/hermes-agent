"""Regression coverage for the code-evidence Kanban completion contract."""
import json
import subprocess
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect, write_txn
from hermes_cli.kanban_pr_acceptance_store import prepare_acceptance, record_acceptance


def _make_temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with a commit and return its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True)
    return repo


def _metadata_with_receipt(commit_sha: str) -> dict:
    return {
        "commit_sha": commit_sha,
        "summary": "All tests passed: 12 passed, 0 failed",
    }


def test_code_evidence_accepts_good_commit_clean_tree_and_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    head = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%H"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    with connect() as conn:
        tid = kb.create_task(
            conn,
            title="Code task",
            completion_contract="code-evidence",
            workspace_kind="worktree",
            workspace_path=str(repo),
            assignee="developer",
        )
        assert kb.complete_task(conn, tid, summary="Implemented feature", metadata=_metadata_with_receipt(head))
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "done"


def test_code_evidence_rejects_bad_sha(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    with connect() as conn:
        tid = kb.create_task(
            conn,
            title="Code task",
            completion_contract="code-evidence",
            workspace_kind="worktree",
            workspace_path=str(repo),
            assignee="developer",
        )
        assert not kb.complete_task(conn, tid, summary="Attempted fix", metadata={"commit_sha": "deadbeef" * 5, "summary": "12 passed"})
        task = kb.get_task(conn, tid)
        assert task is not None and task.status != "done"
        receipts = conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,)
        ).fetchall()
        assert receipts
        payload = json.loads(receipts[-1][0])
        assert not payload["ok"]
        assert "commit_sha_resolves" in payload["classification"]


def test_code_evidence_rejects_a_real_but_stale_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    stale_sha = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%H"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "next.txt").write_text("second commit\n")
    subprocess.run(["git", "add", "next.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Second commit"], cwd=repo, check=True)
    with connect() as conn:
        tid = kb.create_task(
            conn,
            title="Code task",
            completion_contract="code-evidence",
            workspace_kind="worktree",
            workspace_path=str(repo),
            assignee="developer",
        )
        assert not kb.complete_task(conn, tid, summary="Attempted fix", metadata=_metadata_with_receipt(stale_sha))
        task = kb.get_task(conn, tid)
        assert task is not None and task.status != "done"


def test_code_evidence_rejects_dirty_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    head = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%H"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    # Make the tree dirty
    (repo / "dirty.txt").write_text("uncommitted")
    with connect() as conn:
        tid = kb.create_task(
            conn,
            title="Code task",
            completion_contract="code-evidence",
            workspace_kind="worktree",
            workspace_path=str(repo),
            assignee="developer",
        )
        assert not kb.complete_task(conn, tid, summary="Attempted fix", metadata=_metadata_with_receipt(head))
        task = kb.get_task(conn, tid)
        assert task is not None and task.status != "done"
        receipts = conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,)
        ).fetchall()
        assert receipts
        payload = json.loads(receipts[-1][0])
        assert not payload["ok"]
        assert "clean_tree" in payload["classification"]


def test_code_evidence_rejects_missing_test_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    head = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%H"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    with connect() as conn:
        tid = kb.create_task(
            conn,
            title="Code task",
            completion_contract="code-evidence",
            workspace_kind="worktree",
            workspace_path=str(repo),
            assignee="developer",
        )
        # No test receipt pattern in metadata
        assert not kb.complete_task(conn, tid, summary="All good!", metadata={"commit_sha": head, "summary": "All good!"})
        task = kb.get_task(conn, tid)
        assert task is not None and task.status != "done"
        receipts = conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,)
        ).fetchall()
        assert receipts
        payload = json.loads(receipts[-1][0])
        assert not payload["ok"]
        assert "test_receipt" in payload["classification"]


def test_missing_code_evidence_contract_fail_closed(tmp_path, monkeypatch):
    """Developer + worktree + contract != code-evidence -> fail closed with missing_code_evidence_contract."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    with connect() as conn:
        # Missing contract (None)
        tid = kb.create_task(
            conn,
            title="Code task no contract",
            completion_contract=None,
            workspace_kind="worktree",
            workspace_path=str(repo),
            assignee="developer",
        )
        assert not kb.complete_task(conn, tid, summary="12 passed", metadata={"commit_sha": "a" * 40, "summary": "12 passed"})
        task = kb.get_task(conn, tid)
        assert task is not None and task.status != "done"
        receipts = conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,)
        ).fetchall()
        assert receipts
        payload = json.loads(receipts[-1][0])
        assert not payload["ok"]
        assert payload["classification"] == "missing_code_evidence_contract"

        # local-only contract
        tid2 = kb.create_task(
            conn,
            title="Code task local-only",
            completion_contract="local-only",
            workspace_kind="dir",
            workspace_path=str(repo),
            assignee="developer",
        )
        assert not kb.complete_task(conn, tid2, summary="12 passed", metadata={"commit_sha": "a" * 40, "summary": "12 passed"})
        task2 = kb.get_task(conn, tid2)
        assert task2 is not None and task2.status != "done"
        receipts2 = conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid2,)
        ).fetchall()
        assert receipts2
        payload2 = json.loads(receipts2[-1][0])
        assert not payload2["ok"]
        assert payload2["classification"] == "missing_code_evidence_contract"


def test_non_developer_not_affected(tmp_path, monkeypatch):
    """Non-developer assignee completes normally regardless of contract."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    with connect() as conn:
        tid = kb.create_task(
            conn,
            title="QA task",
            completion_contract="local-only",
            workspace_kind="worktree",
            workspace_path=str(repo),
            assignee="qa-analyst",
        )
        assert kb.complete_task(conn, tid, summary="done")
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "done"


def test_non_worktree_not_affected(tmp_path, monkeypatch):
    """Non-worktree/dir workspace_kind completes normally."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    with connect() as conn:
        tid = kb.create_task(
            conn,
            title="Scratch task",
            completion_contract="local-only",
            workspace_kind="scratch",
            workspace_path=None,
            assignee="developer",
        )
        assert kb.complete_task(conn, tid, summary="done")
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "done"


def test_qa_live_evidence_uses_its_own_artifact_gate(tmp_path, monkeypatch):
    """QA evidence is rejected by its own artifact gate, not code evidence."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    with connect() as conn:
        tid = kb.create_task(
            conn,
            title="QA task",
            completion_contract="qa-live-evidence",
            assignee="qa-analyst",
        )
        # qa-live-evidence has its own tamper-evident artifact validation.
        # but it should not be intercepted by the code-evidence classification rule
        assert not kb.complete_task(conn, tid, summary="Incomplete QA", metadata={"cron_before_enabled": False})
        task = kb.get_task(conn, tid)
        assert task is not None and task.status != "done"
        # The failure should be from qa-live-evidence validation, not code-evidence
        receipts = conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,)
        ).fetchall()
        assert receipts
        payload = json.loads(receipts[-1][0])
        assert "evidence_artifact" in payload.get("detail", "")


def _bootstrap_task(conn, repo):
    return kb.create_task(
        conn,
        title="One-time bootstrap task",
        completion_contract="local-only",
        workspace_kind="worktree",
        workspace_path=str(repo),
        assignee="developer",
    )


def test_live_bootstrap_grant_allows_one_local_only_completion_and_is_consumed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    with connect() as conn:
        task_id = _bootstrap_task(conn, repo)
        now = int(time.time())
        conn.execute(
            "INSERT INTO kanban_bootstrap_grants(task_id, granted_by, granted_at, expires_at) VALUES (?, ?, ?, ?)",
            (task_id, "administrator", now, now + 60),
        )
        assert kb.complete_task(conn, task_id, summary="Bootstrap implementation complete")
        task = kb.get_task(conn, task_id)
        assert task is not None and task.status == "done"
        grant = conn.execute(
            "SELECT consumed_at FROM kanban_bootstrap_grants WHERE task_id=?", (task_id,),
        ).fetchone()
        assert grant is not None and grant[0] is not None


@pytest.mark.parametrize("grant_state", ["expired", "consumed"])
def test_expired_or_consumed_bootstrap_grant_fails_closed(tmp_path, monkeypatch, grant_state):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    with connect() as conn:
        task_id = _bootstrap_task(conn, repo)
        now = int(time.time())
        expires_at = now - 1 if grant_state == "expired" else now + 60
        consumed_at = now if grant_state == "consumed" else None
        conn.execute(
            "INSERT INTO kanban_bootstrap_grants(task_id, granted_by, granted_at, expires_at, consumed_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, "administrator", now, expires_at, consumed_at),
        )
        assert not kb.complete_task(conn, task_id, summary="Attempt bootstrap completion")
        task = kb.get_task(conn, task_id)
        assert task is not None and task.status != "done"


def test_bootstrap_grant_cannot_be_consumed_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    repo = _make_temp_git_repo(tmp_path)
    with connect() as conn:
        task_id = _bootstrap_task(conn, repo)
        now = int(time.time())
        conn.execute(
            "INSERT INTO kanban_bootstrap_grants(task_id, granted_by, granted_at, expires_at) VALUES (?, ?, ?, ?)",
            (task_id, "administrator", now, now + 60),
        )
        acceptance = prepare_acceptance(conn, task_id, None, {})
        assert acceptance and acceptance[1]["bootstrap_grant"]
        with write_txn(conn):
            assert record_acceptance(conn, task_id, acceptance)
        with write_txn(conn):
            assert not record_acceptance(conn, task_id, acceptance)


def test_bootstrap_grant_schema_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    with connect() as conn:
        conn.executescript(kb.SCHEMA_SQL)
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kanban_bootstrap_grants'"
        ).fetchone() is not None

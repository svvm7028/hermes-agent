from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


def _setup_board(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()


def _ready(*, assignee: str = "developer", workspace_kind: str = "worktree") -> dict:
    return {
        "invest": {
            "independent": "The change has no unfinished sibling dependency.",
            "negotiable": "Implementation detail can vary while outcome remains fixed.",
            "valuable": "It prevents an underspecified delivery handoff.",
            "estimable": "The bounded validation and storage path is known.",
            "small": "One creation-time admission control.",
            "testable": "Missing or malformed records reject; valid records persist.",
        },
        "card_contract": {
            "problem_outcome": "Require a durable readiness record before routing work.",
            "behavior_slice": "Validate one new DOP/EM-created developer card.",
            "baseline": "Cards currently accept only prose bodies.",
            "scope": {"in": ["Creation admission"], "out": ["Legacy-card retrofit"]},
            "dependencies": {"order": ["Create after readiness validation"]},
            "acceptance_criteria": ["Incomplete record is rejected", "Complete record is stored"],
            "definition_of_done": ["Tests cover reject and accept paths"],
            "evidence_required": ["Stored record read-back", "Focused test result"],
            "risk_rollback": {
                "risk": "A malformed contract blocks dispatch.",
                "rollback": "Remove the new admission requirement without deleting existing tasks.",
            },
            "escalation_ownership": "DOP/EM escalates ambiguous scope to the CEO.",
            "next_action": "Route to the declared developer workspace.",
        },
        "routing": {"assignee": assignee, "workspace_kind": workspace_kind},
    }


def test_dop_em_card_without_readiness_fails_closed(tmp_path, monkeypatch):
    _setup_board(tmp_path, monkeypatch)
    with kbc.connect_closing() as conn:
        with pytest.raises(ValueError, match="delivery_readiness must be an object"):
            kb.create_task(
                conn, title="Missing admission record", assignee="developer",
                workspace_kind="worktree", created_by="director-products",
            )
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


def test_dop_em_card_rejects_incomplete_or_mismatched_readiness(tmp_path, monkeypatch):
    _setup_board(tmp_path, monkeypatch)
    with kbc.connect_closing() as conn:
        incomplete = _ready()
        del incomplete["invest"]["small"]
        with pytest.raises(ValueError, match=r"invest.small"):
            kb.create_task(
                conn, title="Incomplete admission record", assignee="developer",
                workspace_kind="worktree", created_by="director-products",
                delivery_readiness=incomplete,
            )
        mismatched = _ready(assignee="qa")
        with pytest.raises(ValueError, match="routing.assignee must match"):
            kb.create_task(
                conn, title="Mismatched routing", assignee="developer",
                workspace_kind="worktree", created_by="director-products",
                delivery_readiness=mismatched,
            )


def test_dop_em_card_persists_normalized_readiness_for_readback(tmp_path, monkeypatch):
    _setup_board(tmp_path, monkeypatch)
    with kbc.connect_closing() as conn:
        record = _ready()
        task_id = kb.create_task(
            conn, title="Admitted delivery card", assignee="developer",
            workspace_kind="worktree", created_by="director-products",
            delivery_readiness=record,
        )
        assert kb.get_delivery_readiness(conn, task_id) == record
        assert conn.execute(
            "SELECT count(*) FROM kanban_delivery_readiness WHERE task_id=?", (task_id,)
        ).fetchone()[0] == 1


def test_non_dop_creator_is_not_retroactively_subject_to_new_gate(tmp_path, monkeypatch):
    _setup_board(tmp_path, monkeypatch)
    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="Legacy-compatible", assignee="developer")
        assert kb.get_delivery_readiness(conn, task_id) is None


def test_tool_path_uses_served_dop_identity_and_shows_stored_record(tmp_path, monkeypatch):
    root = tmp_path / "hroot"
    (root / "profiles" / "director-products").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.kanban_tools import _handle_create, _handle_show

    token = set_hermes_home_override(root / "profiles" / "director-products")
    try:
        failed = json.loads(_handle_create({
            "title": "Missing structured admission", "assignee": "developer", "workspace_kind": "worktree",
        }))
        assert "delivery_readiness must be an object" in failed["error"]
        created = json.loads(_handle_create({
            "title": "Structured admission", "assignee": "developer", "workspace_kind": "worktree",
            "delivery_readiness": _ready(),
        }))
        assert created["ok"], created
        shown = json.loads(_handle_show({"task_id": created["task_id"]}))
        assert shown["delivery_readiness"] == _ready()
    finally:
        reset_hermes_home_override(token)

"""Regression coverage for artifact-backed generic QA acceptance."""
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect
from hermes_cli.qa_evidence_artifact import write_qa_evidence_artifact
from hermes_cli.qa_harness import HARNESS_STANDARD


def _route():
    return {
        "initial_provider": "custom", "initial_model": "nemotron-3-ultra:cloud",
        "actual_provider": "custom", "actual_model": "gpt-oss-96k:latest",
        "transitions": [{"from_provider": "custom", "from_model": "nemotron-3-ultra:cloud",
                         "to_provider": "custom", "to_model": "gpt-oss-96k:latest", "reason": "monthly_limit"}],
        "usage": {"input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
                  "context_window_tokens": 98304, "cost_status": "unknown", "cost_usd": None},
    }


def _evidence():
    return {
        "harness_standard": HARNESS_STANDARD,
        "timeout_seconds": 120,
        "max_consecutive_failures": 3,
        "configured_route": [{"provider": "custom", "model": "nemotron-3-ultra:cloud"}],
        "scenario_matrix": [{"id": "smoke", "repetitions": 2, "role": None}],
        "environment_before": {"cron_enabled": False},
        "environment_after": {"cron_enabled": False},
        "live_runs": [
            {"scenario_id": "smoke", "repetition": repetition, "timed_out": False,
             "exit_code": 0, "receipt_error": None, "passed": True,
             "latency_seconds": 0.1, "execution_route": _route()}
            for repetition in (1, 2)
        ],
        "expected_run_count": 2,
        "completed_run_count": 2,
        "passed_run_count": 2,
        "terminal_state": "complete",
    }


def _metadata(tmp_path, task_id, run_id=None, evidence=None):
    reference = write_qa_evidence_artifact(
        tmp_path / "qa-live-evidence.json", task_id=task_id, run_id=run_id,
        candidate_sha="a" * 40, evidence=evidence or _evidence(),
    )
    return {"evidence_artifact": reference}


def _receipt(conn, task_id):
    return conn.execute(
        "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance' ORDER BY id DESC", (task_id,)
    ).fetchone()[0]


def test_live_qa_contract_accepts_verified_generic_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    with connect() as conn:
        tid = kb.create_task(conn, title="QA", completion_contract="qa-live-evidence")
        assert kb.complete_task(conn, tid, summary="QA complete", metadata=_metadata(tmp_path, tid))
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "done"


def test_live_qa_contract_rejects_tampered_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    with connect() as conn:
        tid = kb.create_task(conn, title="QA", completion_contract="qa-live-evidence")
        metadata = _metadata(tmp_path, tid)
        with open(metadata["evidence_artifact"]["path"], "ab") as handle:
            handle.write(b" ")
        assert not kb.complete_task(conn, tid, summary="Tampered", metadata=metadata)
        assert "sha256 does not match" in _receipt(conn, tid)


def test_live_qa_contract_rejects_wrong_task_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    with connect() as conn:
        tid = kb.create_task(conn, title="QA", completion_contract="qa-live-evidence")
        assert not kb.complete_task(conn, tid, summary="Wrong identity", metadata=_metadata(tmp_path, "t_wrong"))
        assert "task_id/run_id does not match" in _receipt(conn, tid)


def test_live_qa_contract_rejects_missing_route(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    evidence = _evidence()
    del evidence["live_runs"][0]["execution_route"]
    with connect() as conn:
        tid = kb.create_task(conn, title="QA", completion_contract="qa-live-evidence")
        assert not kb.complete_task(conn, tid, summary="Missing route", metadata=_metadata(tmp_path, tid, evidence=evidence))
        assert "execution_route is required" in _receipt(conn, tid)


def test_live_qa_contract_rejects_coverage_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    evidence = _evidence()
    evidence["live_runs"].pop()
    evidence["completed_run_count"] = 1
    evidence["passed_run_count"] = 1
    with connect() as conn:
        tid = kb.create_task(conn, title="QA", completion_contract="qa-live-evidence")
        assert not kb.complete_task(conn, tid, summary="Partial", metadata=_metadata(tmp_path, tid, evidence=evidence))
        assert "scenario coverage mismatch" in _receipt(conn, tid)


def test_live_qa_contract_rejects_circuit_open(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    evidence = _evidence()
    evidence["terminal_state"] = "circuit_open"
    with connect() as conn:
        tid = kb.create_task(conn, title="QA", completion_contract="qa-live-evidence")
        assert not kb.complete_task(conn, tid, summary="Circuit open", metadata=_metadata(tmp_path, tid, evidence=evidence))
        assert "terminal_state must be complete" in _receipt(conn, tid)

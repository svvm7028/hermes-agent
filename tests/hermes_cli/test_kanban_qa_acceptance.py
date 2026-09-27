"""Regression coverage for the structured live-QA Kanban completion contract."""
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect


def _metadata():
    roles = ["bull", "bear", "chief", "risk", "pm", "bull", "bull", "bull", "bull", "bull"]
    runs = [
        {"role": role, "attempt1_schema_valid": True, "final_schema_valid": True,
         "retry_invoked": False, "latency_seconds": 0.1,
         "execution_route": {
             "initial_provider": "custom", "initial_model": "nemotron-3-ultra:cloud",
             "actual_provider": "custom", "actual_model": "gpt-oss-96k:latest",
             "transitions": [{"from_provider": "custom", "from_model": "nemotron-3-ultra:cloud",
                              "to_provider": "custom", "to_model": "gpt-oss-96k:latest", "reason": "monthly_limit"}],
             "usage": {"input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
                       "context_window_tokens": 98304, "cost_status": "unknown", "cost_usd": None},
         }}
        for role in roles
    ]
    return {
        "timeout_seconds": 120,
        "cron_before_enabled": False,
        "cron_after_enabled": False,
        "configured_route": [
            {"provider": "custom", "model": "nemotron-3-ultra:cloud"},
            {"provider": "openrouter", "model": "nvidia/nemotron-3-ultra-550b-a55b"},
            {"provider": "nvidia", "model": "nvidia/nemotron-3-ultra-550b-a55b"},
            {"provider": "custom", "model": "gpt-oss-96k:latest"},
        ],
        "live_runs": runs,
        "first_attempt_schema_valid_count": 10,
        "final_schema_valid_count": 10,
        "evidence_artifact": "qa-live-evidence.json",
    }


def test_live_qa_contract_rejects_incomplete_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    with connect() as conn:
        tid = kb.create_task(conn, title="QA", completion_contract="qa-live-evidence")
        assert not kb.complete_task(conn, tid, summary="Incomplete QA", metadata={"cron_before_enabled": False})
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "ready"
        receipt = conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,)
        ).fetchone()
        assert receipt is not None and "live_runs must contain at least 10 records" in receipt[0]


def test_live_qa_contract_accepts_complete_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    with connect() as conn:
        tid = kb.create_task(conn, title="QA", completion_contract="qa-live-evidence")
        assert kb.complete_task(conn, tid, summary="QA complete", metadata=_metadata())
        task = kb.get_task(conn, tid)
        assert task is not None and task.status == "done"


def test_live_qa_contract_rejects_missing_route_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    kb.init_db()
    metadata = _metadata()
    del metadata["live_runs"][0]["execution_route"]
    with connect() as conn:
        tid = kb.create_task(conn, title="QA", completion_contract="qa-live-evidence")
        assert not kb.complete_task(conn, tid, summary="Missing route", metadata=metadata)
        receipt = conn.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='pr_acceptance'", (tid,)
        ).fetchone()
        assert receipt is not None and "execution_route is required" in receipt[0]

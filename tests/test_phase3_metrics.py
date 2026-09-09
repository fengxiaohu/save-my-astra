import json
from pathlib import Path

from eval.phase3_metrics import collect_events
from eval.phase3_report import summarize_runs, write_phase3_report
from eval.runtimes.spawn_scan import count_spawns


def _usage(thread_id: str, turn_id: str, total: int) -> dict:
    values = {"input_tokens": total - 1, "output_tokens": 1, "total_tokens": total}
    return {
        "type": "token_usage_record",
        "payload": {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "response_id": f"response-{turn_id}",
            "usage": values,
            "turn_token_usage": values,
            "thread_token_usage": values,
        },
    }


def test_spawn_scan_counts_successful_structured_child_once():
    events = [
        {
            "type": "event_msg",
            "payload": {
                "type": "collab_agent_spawn_begin",
                "call_id": "call-1",
                "sender_thread_id": "parent",
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "collab_agent_spawn_end",
                "call_id": "call-1",
                "sender_thread_id": "parent",
                "new_thread_id": "child",
                "status": "success",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "spawn_agent was mentioned in prose",
            },
        },
    ]
    payload = "\n".join(json.dumps(event) for event in events)
    assert count_spawns(payload) == 1


def test_collect_events_deduplicates_spawn_and_usage():
    parent = "parent"
    child = "child"
    events = [
        {"type": "session_meta", "payload": {"session_id": parent}},
        {
            "type": "event_msg",
            "payload": {
                "type": "collab_agent_spawn_begin",
                "call_id": "call-1",
                "sender_thread_id": parent,
                "requested_model": "gpt-5.6-luna",
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "collab_agent_spawn_end",
                "call_id": "call-1",
                "sender_thread_id": parent,
                "new_thread_id": child,
                "model": "gpt-5.6-luna",
                "status": "success",
            },
        },
        # A repeated end event must not create another child.
        {
            "type": "event_msg",
            "payload": {
                "type": "collab_agent_spawn_end",
                "call_id": "call-1",
                "sender_thread_id": parent,
                "new_thread_id": child,
                "model": "gpt-5.6-luna",
                "status": "success",
            },
        },
        _usage(parent, "turn-parent", 10),
        _usage(child, "turn-child", 5),
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "thread_id": parent,
                "turn_id": "turn-parent",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 9,
                        "output_tokens": 1,
                        "total_tokens": 10,
                    },
                    "total_token_usage": {
                        "input_tokens": 9,
                        "output_tokens": 1,
                        "total_tokens": 10,
                    },
                },
            },
        },
        {"type": "event_msg", "payload": {"type": "collection_complete"}},
    ]
    metrics = collect_events(events)
    assert metrics["spawn_attempts"] == 1
    assert metrics["spawn_count"] == 1
    assert metrics["children"] == [
        {
            "thread_id": child,
            "parent_thread_id": parent,
            "model": "gpt-5.6-luna",
            "model_source": "runtime",
            "status": "completed",
        }
    ]
    assert metrics["parent_tokens"] == 10
    assert metrics["child_tokens"] == 5
    assert metrics["total_tokens"] == 15
    assert metrics["observed_tokens"] == 15
    assert metrics["observation_complete"] is True
    assert metrics["usage_complete"] is True
    assert metrics["usd_estimate"] is None


def test_requested_model_does_not_confirm_child_identity():
    events = [
        {"type": "session_meta", "payload": {"session_id": "parent"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "collab_agent_spawn_end",
                "call_id": "call-1",
                "sender_thread_id": "parent",
                "new_thread_id": "child",
                "requested_model": "gpt-5.6-luna",
                "status": "success",
            },
        },
        {"type": "event_msg", "payload": {"type": "collection_complete"}},
    ]
    metrics = collect_events(events)
    assert metrics["spawn_count"] == 1
    assert metrics["children"][0]["model"] == "gpt-5.6-luna"
    assert metrics["children"][0]["model_source"] == "requested"
    assert metrics["observation_complete"] is False


def test_created_child_that_later_fails_stays_in_spawn_count():
    events = [
        {"type": "session_meta", "payload": {"session_id": "parent"}},
        {
            "type": "collab_agent_spawn_end",
            "call_id": "call-1",
            "sender_thread_id": "parent",
            "new_thread_id": "child",
            "model": "gpt-5.6-luna",
            "status": "success",
        },
        {
            "type": "collab_agent_spawn_end",
            "call_id": "call-1-result",
            "sender_thread_id": "parent",
            "new_thread_id": "child",
            "model": "gpt-5.6-luna",
            "status": "failed",
        },
        {"type": "event_msg", "payload": {"type": "collection_complete"}},
    ]
    metrics = collect_events(events)
    assert metrics["spawn_count"] == 1
    assert metrics["children"][0]["status"] == "failed"


def test_root_only_truncated_stream_is_incomplete():
    events = [
        {"type": "session_meta", "payload": {"session_id": "parent"}},
        _usage("parent", "turn-parent", 10),
    ]
    metrics = collect_events(events)
    assert metrics["parent_tokens"] == 10
    assert metrics["total_tokens"] is None
    assert metrics["observed_tokens"] == 10
    assert metrics["observation_complete"] is False
    assert metrics["usage_complete"] is False
    assert any("complete" in error for error in metrics["errors"])


def test_completed_turn_is_not_proof_of_complete_collection():
    metrics = collect_events([{"type": "session_meta", "id": "root"},
                              {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 1}}])
    assert metrics["observation_complete"] is False
    assert metrics["usage_complete"] is False


def _record(task: str, arm: str, attempt_id: str, *, passed: bool | None, status: str = "completed", replacement_of=None, parent_tokens=None):
    return {
        "task": task,
        "arm": arm,
        "attempt_id": attempt_id,
        "status": status,
        "verifier": {"passed": passed},
        "metrics": {
            "spawn_count": 0,
            "children": [],
            "observation_complete": True,
            "usage_complete": parent_tokens is not None,
            "parent_tokens": parent_tokens,
            "child_tokens": None,
            "total_tokens": parent_tokens,
            "observed_tokens": parent_tokens,
            "usd_estimate": None,
            "errors": [],
        },
        "timing": {"agent_seconds": 1, "end_to_end_seconds": 1},
        "replacement_of": replacement_of,
    }


def test_report_preserves_not_run_and_selects_replacement(tmp_path: Path):
    records = [
        _record("session-window-debug", "astra-solo", "old", passed=False),
        _record(
            "session-window-debug",
            "astra-solo",
            "replacement",
            passed=True,
            replacement_of="old",
            parent_tokens=10,
        ),
        _record("session-window-debug", "astra-luna", "routed", passed=True, parent_tokens=8),
        _record("payments-pipeline-fix", "astra-solo", "solo-2", passed=True, parent_tokens=10),
        _record("payments-pipeline-fix", "astra-luna", "luna-2", passed=True, parent_tokens=8),
        _record("bun-sourcemap-leak", "astra-solo", "solo-3", passed=None, status="not_run"),
        _record("bun-sourcemap-leak", "astra-luna", "luna-3", passed=None, status="not_run"),
        _record("nextjs-performance", "astra-solo", "solo-4", passed=None, status="not_run"),
        _record("nextjs-performance", "astra-luna", "luna-4", passed=None, status="not_run"),
    ]
    summary = summarize_runs(records)
    selected = {(record["task"], record["arm"]): record for record in summary["effective_records"]}
    assert selected[("session-window-debug", "astra-solo")]["attempt_id"] == "replacement"
    assert summary["attempted_runs"] == 4
    assert len(summary["not_run"]) == 4
    assert summary["pairs"][0]["quality"] == "both_pass"
    report = write_phase3_report(tmp_path / "phase3a", records, {"protocol": "test"})
    assert report.name == "REPORT.md"
    assert report.exists()
    assert report.with_name("summary.json").exists()


def test_report_does_not_treat_aborted_pair_as_complete():
    records = [
        _record("session-window-debug", "astra-solo", "solo", passed=True, status="budget_aborted", parent_tokens=10),
        _record("session-window-debug", "astra-luna", "routed", passed=True, parent_tokens=8),
    ]
    summary = summarize_runs(records)
    pair = summary["pairs"][0]
    assert pair["quality"] == "both_pass"
    assert pair["complete"] is False
    assert summary["gates"]["completeness"] is False
    assert summary["parent_signal"]["passes"] is False


def test_report_rejects_baseline_child_and_recursive_child():
    baseline = _record("session-window-debug", "astra-solo", "solo", passed=True, parent_tokens=10)
    baseline["metrics"].update(
        {
            "spawn_count": 1,
            "children": [
                {
                    "thread_id": "child",
                    "parent_thread_id": "parent",
                    "model": "gpt-5.6-luna",
                    "model_source": "runtime",
                    "status": "completed",
                }
            ],
        }
    )
    recursive = _record("payments-pipeline-fix", "astra-luna", "routed", passed=True, parent_tokens=8)
    recursive["metrics"].update(
        {
            "spawn_count": 2,
            "children": [
                {
                    "thread_id": "child-a",
                    "parent_thread_id": "parent",
                    "model": "gpt-5.6-luna",
                    "model_source": "runtime",
                    "status": "completed",
                },
                {
                    "thread_id": "child-b",
                    "parent_thread_id": "child-a",
                    "model": "gpt-5.6-luna",
                    "model_source": "runtime",
                    "status": "completed",
                },
            ],
        }
    )
    summary = summarize_runs([baseline, recursive])
    assert summary["gates"]["identity"] is False
    assert summary["identity"]["issues"]


def test_report_synthesizes_missing_planned_cells_as_not_run():
    summary = summarize_runs([])
    assert len(summary["not_run"]) == 8
    assert summary["attempted_runs"] == 0

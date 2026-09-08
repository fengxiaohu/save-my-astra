"""Small helpers for counting structured agent creation events.

The runner receives both the JSON objects emitted by ``codex exec --json`` and
rollout JSONL objects. A textual mention of ``spawn_agent`` is not evidence
that a child was created, so this module deliberately ignores strings except
when they contain parseable JSON objects.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


_SPAWN_TOOLS = {
    "spawn_agent",
    "spawn_agents",
    "spawn-agent",
    "spawn-agents",
}
_SUCCESS_STATES = {
    "complete",
    "completed",
    "created",
    "ok",
    "success",
    "succeeded",
}
_FAILURE_STATES = {
    "abort",
    "aborted",
    "cancel",
    "cancelled",
    "canceled",
    "error",
    "failed",
    "failure",
    "interrupted",
    "rejected",
}


def _json_objects(payload: Any) -> Iterator[dict[str, Any]]:
    """Yield dictionaries from a dict/list or a JSONL string."""

    if isinstance(payload, dict):
        yield payload
        return
    if isinstance(payload, (list, tuple)):
        for value in payload:
            yield from _json_objects(value)
        return
    if not isinstance(payload, str):
        return
    text = payload.strip()
    if not text:
        return
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None
    if decoded is not None:
        yield from _json_objects(decoded)
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            continue
        yield from _json_objects(decoded)


def _walk_dicts(value: Any) -> Iterator[dict[str, Any]]:
    """Walk nested event wrappers, yielding each dictionary once by identity."""

    seen: set[int] = set()

    def walk(current: Any) -> Iterator[dict[str, Any]]:
        if isinstance(current, dict):
            marker = id(current)
            if marker in seen:
                return
            seen.add(marker)
            yield current
            for child in current.values():
                yield from walk(child)
        elif isinstance(current, (list, tuple)):
            for child in current:
                yield from walk(child)

    yield from walk(value)


def _first(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        candidate = value.get(key)
        if candidate is not None:
            return candidate
    return None


def _as_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, (list, tuple, set)):
        return []
    ids: list[str] = []
    for entry in value:
        if isinstance(entry, str) and entry:
            ids.append(entry)
        elif isinstance(entry, dict):
            item_id = _first(
                entry,
                "thread_id",
                "agent_thread_id",
                "new_thread_id",
                "child_thread_id",
                "id",
            )
            if isinstance(item_id, str) and item_id:
                ids.append(item_id)
    return ids


def _state(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_successful(value: dict[str, Any], *, kind: str = "") -> bool:
    state = _state(
        _first(value, "status", "state", "result", "outcome", "phase")
    )
    if state in _FAILURE_STATES:
        return False
    if state in _SUCCESS_STATES:
        return True
    # End events can omit a status while carrying the server-assigned id. A
    # begin event cannot be treated as a successful creation on that basis.
    return kind.endswith("_end") or kind in {"child_created", "spawned"}


def _candidate_child_ids(item: dict[str, Any], wrapper: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for source in (item, wrapper):
        for key in (
            "new_thread_id",
            "thread_id",
            "child_thread_id",
            "agent_thread_id",
            "receiver_thread_id",
            "receiver_thread_ids",
            "thread_ids",
            "children",
            "receiver_agents",
        ):
            ids.extend(_as_ids(source.get(key)))
    return list(dict.fromkeys(ids))


def _successful_spawn_records(payload: Any) -> list[tuple[str, str]]:
    """Return ``(dedupe key, child thread id)`` for successful child creates."""

    records: list[tuple[str, str]] = []
    for wrapper in _json_objects(payload):
        for item in _walk_dicts(wrapper):
            raw_kind = _first(item, "type", "event", "event_type")
            kind = _state(raw_kind)
            candidate = item.get("item")
            child_item = candidate if isinstance(candidate, dict) else item
            item_kind = _state(
                _first(child_item, "type", "event", "event_type")
            )
            tool = _state(_first(child_item, "tool", "name", "action"))
            spawn_end = kind in {
                "collab_agent_spawn_end",
                "collab_agent_spawned",
                "child_created",
                "spawned",
            }
            spawn_item = item_kind in {"collab_agent_spawn", "agent_spawn"}
            spawn_tool = item_kind == "collab_tool_call" and tool in _SPAWN_TOOLS
            direct_spawn = kind in {
                "collab_agent_spawn_end",
                "collab_agent_spawned",
                "child_created",
                "spawned",
            }
            if not (spawn_end or spawn_item or spawn_tool or direct_spawn):
                continue
            ids = _candidate_child_ids(child_item, item)
            if not ids:
                continue
            if not _is_successful(child_item, kind=kind or item_kind):
                continue
            for child_id in ids:
                # The thread id is the identity of a successful child. The
                # same child is reported by begin/end and item wrappers with
                # different item ids, so deduping by call id would count it
                # twice.
                records.append((child_id, child_id))
    return records


def count_spawns(payload: Any) -> int:
    """Count unique, successfully created child threads in structured data."""

    return len({key for key, _ in _successful_spawn_records(payload)})


__all__ = ["count_spawns"]

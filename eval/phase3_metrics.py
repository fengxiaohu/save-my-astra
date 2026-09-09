"""Phase 3A routing and usage measurement.

This module consumes decoded Codex JSONL objects.  It intentionally keeps the
measurement boundary conservative: a requested or configured worker model is
reported as such and is never promoted to runtime-confirmed identity.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any


EVENT_SCHEMA_VERSION = "phase3a.events.v1"

_SUCCESS = {"complete", "completed", "created", "ok", "success", "succeeded"}
_FAILURE = {
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
_TERMINAL = _SUCCESS | _FAILURE
_SPAWN_TOOLS = {"spawn_agent", "spawn_agents", "spawn-agent", "spawn-agents"}
_TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def _first(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        item = value.get(key)
        if item is not None:
            return item
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _json_dicts(value: Any) -> Iterator[dict[str, Any]]:
    """Walk dictionaries in a decoded event without inspecting prose."""

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


def _event_type(value: dict[str, Any]) -> str:
    return _lower(_first(value, "type", "event", "event_type"))


def _payload_view(value: dict[str, Any], *, kind: str | None = None) -> dict[str, Any]:
    """Flatten the common rollout ``{type, payload:{...}}`` envelope."""

    payload = value.get("payload")
    if not isinstance(payload, dict):
        return value
    view = dict(payload)
    outer_kind = kind or _event_type(value)
    if outer_kind and not any(key in view for key in ("type", "event", "event_type")):
        view["type"] = outer_kind
    return view


def _token_map(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    aliases = {
        "input": "input_tokens",
        "cached_input": "cached_input_tokens",
        "cache_write_input": "cache_write_input_tokens",
        "output": "output_tokens",
        "reasoning_output": "reasoning_output_tokens",
        "total": "total_tokens",
    }
    for key in _TOKEN_KEYS:
        number = _as_int(raw.get(key))
        if number is not None:
            result[key] = number
    for source, target in aliases.items():
        if target in result:
            continue
        number = _as_int(raw.get(source))
        if number is not None:
            result[target] = number
    return result


def _token_total(values: dict[str, int]) -> int | None:
    total = values.get("total_tokens")
    if total is not None:
        return total
    # cached input and reasoning output are subsets in Codex's usage schema;
    # adding either would double count.  Derive a total only when the two
    # primary components are both present.
    input_tokens = values.get("input_tokens")
    output_tokens = values.get("output_tokens")
    if input_tokens is not None and output_tokens is not None:
        return input_tokens + output_tokens
    return None


def _merge_values(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = value


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        item = _first(
            value,
            "thread_id",
            "agent_thread_id",
            "new_thread_id",
            "child_thread_id",
            "id",
        )
        return [item] if isinstance(item, str) and item else []
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        result.extend(_ids(item))
    return list(dict.fromkeys(result))


def _status(value: Any, *, default: str = "unknown") -> str:
    state = _lower(value)
    if not state:
        return default
    if state in _SUCCESS:
        return "completed" if state in {"complete", "completed", "success", "succeeded", "ok"} else state
    if state in _FAILURE:
        return "failed" if state in {"error", "failure", "failed", "rejected"} else state
    return state


def _model_from(value: dict[str, Any]) -> tuple[str | None, str]:
    """Return model and evidence source, preferring runtime evidence."""

    for key in (
        "runtime_model",
        "observed_model",
        "actual_model",
        "model_slug",
        "model_id",
    ):
        model = value.get(key)
        if isinstance(model, str) and model:
            return model, "runtime"
    # A plain model on a child lifecycle item is runtime evidence.  Plain
    # model fields on session/turn configuration objects are deliberately not
    # read by the caller as child identity.
    model = value.get("model")
    if isinstance(model, str) and model:
        return model, "runtime"
    for key in ("requested_model", "worker_model", "model_requested"):
        model = value.get(key)
        if isinstance(model, str) and model:
            return model, "requested"
    for key in ("configured_model", "default_model", "model_config"):
        model = value.get(key)
        if isinstance(model, str) and model:
            return model, "configured"
    return None, "unknown"


def _runtime_parent_identity(value: dict[str, Any]) -> tuple[str | None, str | None]:
    """Read explicit runtime root identity, excluding config/request fields."""

    model = None
    for key in ("runtime_model", "observed_model", "actual_model", "model_slug"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            model = candidate
            break
    effort = None
    for key in ("runtime_effort", "observed_effort", "actual_effort"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            effort = candidate
            break
    return model, effort


def _parent_id(value: dict[str, Any]) -> str | None:
    parent = _first(value, "parent_thread_id", "parent_id", "sender_thread_id")
    return parent if isinstance(parent, str) and parent else None


def _child_entries(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract child references without treating the sender as a child."""

    entries: list[dict[str, Any]] = []
    parent = _parent_id(value)
    for key in (
        "new_thread_id",
        "child_thread_id",
        "agent_thread_id",
        "receiver_thread_id",
    ):
        for thread_id in _ids(value.get(key)):
            entries.append({"thread_id": thread_id, "parent_thread_id": parent})
    for thread_id in _ids(value.get("receiver_thread_ids")):
        entries.append({"thread_id": thread_id, "parent_thread_id": parent})
    for key in ("receiver_agents", "children", "agents"):
        raw = value.get(key)
        if isinstance(raw, dict):
            raw = list(raw.values())
        if not isinstance(raw, (list, tuple)):
            continue
        for child in raw:
            if isinstance(child, str):
                entries.append({"thread_id": child, "parent_thread_id": parent})
                continue
            if not isinstance(child, dict):
                continue
            thread_id = _first(
                child,
                "thread_id",
                "agent_thread_id",
                "new_thread_id",
                "child_thread_id",
                "id",
            )
            if not isinstance(thread_id, str) or not thread_id:
                continue
            item = dict(child)
            item["thread_id"] = thread_id
            item.setdefault("parent_thread_id", parent)
            entries.append(item)
    states = value.get("agents_states")
    if isinstance(states, dict):
        for state_key, state_value in states.items():
            state = state_value if isinstance(state_value, dict) else {}
            thread_id = _first(
                state,
                "thread_id",
                "agent_thread_id",
                "new_thread_id",
                "child_thread_id",
            )
            if not isinstance(thread_id, str) or not thread_id:
                thread_id = state_key if isinstance(state_key, str) and state_key else None
            if not thread_id:
                continue
            item = dict(state)
            item["thread_id"] = thread_id
            item.setdefault("parent_thread_id", parent)
            entries.append(item)
    # A normalized child_created event has a direct thread_id.  A generic
    # collab item may use thread_id for its own sender, so only use it when the
    # item/event explicitly says it represents a child.
    kind = _event_type(value)
    if kind in {
        "collab_agent_spawn_end",
        "collab_agent_spawned",
        "child_created",
        "spawned",
    }:
        for thread_id in _ids(value.get("thread_id")):
            entries.append({"thread_id": thread_id, "parent_thread_id": parent})
    return entries


def _candidate_spawn(value: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Return a spawn item and lifecycle phase for one wrapper dictionary."""

    kind = _event_type(value)
    item = value.get("item")
    item_dict = item if isinstance(item, dict) else value
    item_kind = _event_type(item_dict)
    tool = _lower(_first(item_dict, "tool", "name", "action"))
    if kind in {"collab_agent_spawn_begin", "collab_agent_spawn_end"}:
        return value, "begin" if kind.endswith("_begin") else "end"
    if kind in {"child_created", "collab_agent_spawned", "spawned"}:
        return value, "end"
    if item_kind in {"collab_agent_spawn", "agent_spawn"}:
        phase = _lower(_first(item_dict, "phase", "lifecycle", "event"))
        if phase in {"begin", "started", "start", "in_progress"} or kind in {
            "item_started",
            "item.started",
        }:
            return item_dict, "begin"
        return item_dict, "end"
    if item_kind == "collab_tool_call" and tool in _SPAWN_TOOLS:
        if kind in {"item_started", "item.started"} or _lower(item_dict.get("status")) in {
            "in_progress",
            "started",
            "pending",
        }:
            return item_dict, "begin"
        return item_dict, "end"
    # The normalized schema uses an event field while retaining a stable
    # version marker; accepting the marker keeps fixtures auditable.
    if value.get("schema_version") == EVENT_SCHEMA_VERSION and _lower(
        value.get("event")
    ) in {"spawn_attempt", "child_created"}:
        return value, "begin" if _lower(value.get("phase")) == "begin" else "end"
    return None


@dataclass
class _Attempt:
    key: str
    parent_thread_id: str | None = None
    begin: bool = False
    end: bool = False
    status: str = "unknown"
    child_ids: set[str] = field(default_factory=set)


@dataclass
class _Child:
    thread_id: str
    parent_thread_id: str | None = None
    model: str | None = None
    model_source: str = "unknown"
    status: str = "unknown"
    created: bool = False
    failed: bool = False


@dataclass
class _UsageRecord:
    thread_id: str | None
    turn_id: str | None
    response_id: str | None
    delta: dict[str, int]
    cumulative: dict[str, int]
    source: str


def _usage_records(events: list[dict[str, Any]], root_ids: set[str]) -> tuple[list[_UsageRecord], list[str], set[str]]:
    records: list[_UsageRecord] = []
    errors: list[str] = []
    seen_event_ids: set[str] = set()
    seen_anonymous_shapes: set[tuple[Any, ...]] = set()
    active_thread: str | None = next(iter(root_ids), None)

    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"event[{index}] is not an object")
            continue
        # A session/turn object provides useful context for token_count events,
        # whose payload historically omitted thread_id.
        for item in _json_dicts(event):
            kind = _event_type(item)
            body = _payload_view(item, kind=kind)
            if kind in {"session_meta", "thread.started", "thread_started"}:
                candidate = _first(item, "thread_id", "session_id", "id") or _first(
                    body, "thread_id", "session_id", "id"
                )
                if isinstance(candidate, str) and candidate:
                    active_thread = candidate
            if kind in {"turn_context", "turn_context_item"}:
                candidate = _first(item, "thread_id", "session_id") or _first(
                    body, "thread_id", "session_id"
                )
                if isinstance(candidate, str) and candidate:
                    active_thread = candidate

        for item in _json_dicts(event):
            kind = _event_type(item)
            body = _payload_view(item, kind=kind)
            usage_source = body if body is not item else item
            body_kind = _event_type(usage_source)
            usage_kind = None
            if kind == "token_usage_record" or body_kind == "token_usage_record":
                usage_kind = "record"
            elif kind in {"token_count", "token_count_event"} or body_kind in {
                "token_count",
                "token_count_event",
            }:
                usage_kind = "count"
            elif kind in {
                "turn.completed",
                "turn_complete",
                "turn_completed",
                "task_complete",
            } and (
                isinstance(usage_source.get("usage"), dict)
                or isinstance(
                    usage_source.get("turn", {}).get("usage")
                    if isinstance(usage_source.get("turn"), dict)
                    else None,
                    dict,
                )
            ):
                usage_kind = "turn"
            elif kind == "usage" or (
                isinstance(usage_source.get("usage"), dict)
                and _first(usage_source, "thread_id", "turn_id") is not None
            ):
                usage_kind = "generic"
            if usage_kind is None:
                continue

            thread_id = _first(
                usage_source, "thread_id", "session_id", "root_thread_id"
            )
            if not isinstance(thread_id, str) or not thread_id:
                thread_id = active_thread
            if isinstance(thread_id, str) and thread_id:
                active_thread = thread_id
            turn_id = _first(usage_source, "turn_id", "current_turn_id")
            response_id = _first(usage_source, "response_id")
            if not isinstance(turn_id, str):
                turn_id = None
            if not isinstance(response_id, str):
                response_id = None

            usage = usage_source.get("usage")
            if not isinstance(usage, dict) and isinstance(usage_source.get("turn"), dict):
                usage = usage_source["turn"].get("usage")
            info = usage_source.get("info") if isinstance(usage_source.get("info"), dict) else {}
            total_info = info.get("total_token_usage") if isinstance(info, dict) else None
            last_info = info.get("last_token_usage") if isinstance(info, dict) else None
            turn_usage = usage_source.get("turn_token_usage")
            thread_usage = usage_source.get("thread_token_usage")
            if usage_kind == "count" and not isinstance(last_info, dict):
                # Some normalized emitters put the delta directly under usage.
                last_info = usage
            delta_raw = turn_usage if isinstance(turn_usage, dict) else last_info
            if not isinstance(delta_raw, dict):
                delta_raw = usage if isinstance(usage, dict) else {}
            if not delta_raw and isinstance(usage_source.get("delta"), dict):
                delta_raw = usage_source["delta"]
            cumulative_raw = thread_usage if isinstance(thread_usage, dict) else total_info
            if not isinstance(cumulative_raw, dict):
                cumulative_raw = {}
            if not cumulative_raw and isinstance(usage_source.get("cumulative"), dict):
                cumulative_raw = usage_source["cumulative"]
            delta = _token_map(delta_raw)
            cumulative = _token_map(cumulative_raw)
            if not delta and not cumulative:
                errors.append(f"usage event[{index}] has no recognizable token fields")
                continue

            # One response is represented by token_usage_record and token_count
            # in rollout files.  Deduplicate explicit IDs first.  Anonymous
            # records are deduped later using their thread/turn/usage tuple.
            event_key = response_id or (
                f"{thread_id}:{turn_id}" if thread_id and turn_id else None
            )
            turn_marker = (
                f"turn-id:{thread_id}:{turn_id}"
                if thread_id and turn_id
                else None
            )
            if turn_marker and turn_marker in seen_event_ids:
                continue
            if event_key:
                marker = f"{usage_kind}:{event_key}"
                if marker in seen_event_ids:
                    continue
                # token_usage_record and token_count with the same turn are the
                # same usage observation, even though their event kinds differ.
                cross_marker = f"turn:{event_key}"
                if cross_marker in seen_event_ids:
                    continue
                seen_event_ids.add(marker)
                seen_event_ids.add(cross_marker)
                if turn_marker:
                    seen_event_ids.add(turn_marker)
                if thread_id:
                    seen_anonymous_shapes.add(
                        (
                            thread_id,
                            tuple(sorted(delta.items())),
                            tuple(sorted(cumulative.items())),
                        )
                    )
            elif usage_kind == "count" and thread_id:
                # Older token_count events omit turn/response ids.  A
                # token_usage_record immediately adjacent to the same count
                # carries the same delta and cumulative total, so treat the
                # exact tuple as one observation.  Distinct turns with no ids
                # remain inherently unresolvable and are reported as such by
                # the caller's coverage metadata.
                anonymous_marker = (
                    thread_id,
                    tuple(sorted(delta.items())),
                    tuple(sorted(cumulative.items())),
                )
                if anonymous_marker in seen_anonymous_shapes:
                    continue
                seen_anonymous_shapes.add(anonymous_marker)
            records.append(
                _UsageRecord(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    response_id=response_id,
                    delta=delta,
                    cumulative=cumulative,
                    source=usage_kind,
                )
            )
    return records, errors, seen_event_ids


def _aggregate_usage(
    records: list[_UsageRecord],
    root_ids: set[str],
    children: dict[str, _Child],
) -> tuple[int | None, int | None, int | None, int | None, bool, list[str]]:
    errors: list[str] = []
    if not records:
        return None, None, None, None, False, ["no recognizable usage records"]

    by_thread: dict[str, list[_UsageRecord]] = defaultdict(list)
    unknown_records: list[_UsageRecord] = []
    for record in records:
        if record.thread_id:
            by_thread[record.thread_id].append(record)
        else:
            unknown_records.append(record)
    if unknown_records:
        errors.append("usage record missing thread_id")

    thread_totals: dict[str, int] = {}
    incomplete_threads: set[str] = set()
    for thread_id, entries in by_thread.items():
        # Delta records are per-turn values.  Anonymous duplicate snapshots
        # are ignored by their exact usage tuple.
        seen_deltas: set[tuple[Any, ...]] = set()
        deltas: list[int] = []
        cumulative_totals: list[int] = []
        for entry in entries:
            delta_total = _token_total(entry.delta)
            if delta_total is not None:
                marker = (
                    entry.turn_id,
                    entry.response_id,
                    tuple(sorted(entry.delta.items())),
                )
                if marker not in seen_deltas:
                    seen_deltas.add(marker)
                    deltas.append(delta_total)
            cumulative_total = _token_total(entry.cumulative)
            if cumulative_total is not None:
                cumulative_totals.append(cumulative_total)
        if deltas:
            thread_totals[thread_id] = sum(deltas)
        elif cumulative_totals:
            thread_totals[thread_id] = max(cumulative_totals)
        else:
            incomplete_threads.add(thread_id)

    known_child_ids = set(children)
    assigned_ids = set(root_ids) | known_child_ids
    unassigned = set(by_thread) - assigned_ids
    if unassigned:
        errors.append("usage observed for thread without root/child relationship")
    if incomplete_threads:
        errors.append("usage record has no complete token total")

    parent_values = [thread_totals[thread] for thread in root_ids if thread in thread_totals]
    child_values = [thread_totals[thread] for thread in known_child_ids if thread in thread_totals]
    parent_tokens = sum(parent_values) if parent_values else None
    child_tokens = sum(child_values) if child_values else None
    observed_tokens = sum(thread_totals.values()) if thread_totals else None
    complete = bool(
        parent_values
        and not unknown_records
        and not incomplete_threads
        and not unassigned
        and all(thread in thread_totals for thread in known_child_ids)
    )
    total_tokens = parent_tokens + (child_tokens or 0) if complete and parent_tokens is not None else None
    return parent_tokens, child_tokens, total_tokens, observed_tokens, complete, errors


def collect_events(events: list[dict]) -> dict:
    """Collect Phase 3A routing and token metrics from decoded JSONL events."""

    errors: list[str] = []
    root_ids: set[str] = set()
    children: dict[str, _Child] = {}
    attempts: dict[str, _Attempt] = {}
    seen_spawn_events: set[tuple[str, str]] = set()
    observed_spawn_structure = False
    active_thread: str | None = None
    completion_marker = False
    parent_models: set[str] = set()
    parent_efforts: set[str] = set()
    runtime_identity_candidates: list[tuple[str, str | None, str | None, str]] = []

    if not isinstance(events, list):
        events = list(events or [])

    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"event[{index}] is not an object")
            continue
        # Session metadata is authoritative for the root thread id when
        # present.  Configured model fields in this object are intentionally
        # ignored.
        for item in _json_dicts(event):
            kind = _event_type(item)
            body = _payload_view(item, kind=kind)
            body_kind = _event_type(body)
            if kind == "collection_complete" or body_kind == "collection_complete":
                completion_marker = True
            if kind == "session_meta":
                root = _first(
                    item,
                    "thread_id",
                    "session_id",
                    "id",
                    "root_thread_id",
                ) or _first(
                    body,
                    "thread_id",
                    "session_id",
                    "id",
                    "root_thread_id",
                )
                if isinstance(root, str) and root:
                    root_ids.add(root)
                    active_thread = root
                else:
                    errors.append("session_meta has no thread/session id")
            elif kind in {"thread_context", "turn_context"}:
                root = _first(item, "root_thread_id", "thread_id") or _first(
                    body, "root_thread_id", "thread_id"
                )
                if isinstance(root, str) and root and not root_ids:
                    root_ids.add(root)
                    active_thread = root
            elif kind in {"thread.started", "thread_started"}:
                thread = _first(item, "thread_id", "id") or _first(
                    body, "thread_id", "id"
                )
                if isinstance(thread, str) and thread and not root_ids:
                    root_ids.add(thread)
                    active_thread = thread

            # Runtime parent identity must come from an explicit observed
            # field. A configured/requested model in session or turn context
            # is deliberately ignored.
            runtime_model, runtime_effort = _runtime_parent_identity(item)
            if kind == "model_verification":
                candidate_model = item.get("model") or body.get("model")
                candidate_effort = item.get("reasoning_effort") or body.get(
                    "reasoning_effort"
                )
                if isinstance(candidate_model, str) and candidate_model:
                    runtime_model = candidate_model
                if isinstance(candidate_effort, str) and candidate_effort:
                    runtime_effort = candidate_effort
            thread_hint = _first(
                item,
                "thread_id",
                "session_id",
                "root_thread_id",
                "sender_thread_id",
            ) or _first(
                body,
                "thread_id",
                "session_id",
                "root_thread_id",
                "sender_thread_id",
            )
            if runtime_model or runtime_effort:
                runtime_identity_candidates.append(
                    (
                        thread_hint if isinstance(thread_hint, str) else "",
                        runtime_model,
                        runtime_effort,
                        kind,
                    )
                )

        for item in _json_dicts(event):
            candidate = _candidate_spawn(item)
            if candidate is None:
                continue
            spawn, phase = candidate
            observed_spawn_structure = True
            kind = _event_type(spawn)
            attempt_id = _first(spawn, "attempt_id", "call_id", "invocation_id", "item_id", "id")
            child_refs = _child_entries(spawn)
            child_ids = [entry["thread_id"] for entry in child_refs if entry.get("thread_id")]
            if not isinstance(attempt_id, str) or not attempt_id:
                if child_ids:
                    attempt_id = f"child:{child_ids[0]}"
                elif active_thread:
                    attempt_id = f"event:{index}:{active_thread}"
                else:
                    attempt_id = f"event:{index}"
            dedupe_marker = (attempt_id, phase)
            if dedupe_marker in seen_spawn_events:
                continue
            seen_spawn_events.add(dedupe_marker)
            attempt = attempts.setdefault(attempt_id, _Attempt(key=attempt_id))
            parent = _parent_id(spawn)
            if parent:
                attempt.parent_thread_id = parent
                root_ids.add(parent) if not root_ids else None
            if phase == "begin":
                attempt.begin = True
            else:
                attempt.end = True
            state = _status(_first(spawn, "status", "state", "outcome", "result"))
            if state != "unknown":
                attempt.status = state
            model, model_source = _model_from(spawn)
            if phase == "begin" and model:
                model_source = "requested"
            for entry in child_refs:
                thread_id = entry.get("thread_id")
                if not isinstance(thread_id, str) or not thread_id:
                    continue
                attempt.child_ids.add(thread_id)
                child = children.setdefault(thread_id, _Child(thread_id=thread_id))
                child_parent = entry.get("parent_thread_id") or parent
                if child_parent:
                    child.parent_thread_id = child_parent
                entry_model, entry_source = _model_from(entry)
                if phase == "begin" and entry_model:
                    entry_source = "requested"
                use_model, use_source = entry_model or model, entry_source if entry_model else model_source
                if use_model:
                    if use_source == "runtime" or child.model_source != "runtime":
                        child.model = use_model
                        child.model_source = use_source
                child_status = _status(
                    _first(entry, "status", "state", "outcome")
                    or _first(spawn, "status", "state", "outcome")
                    or ("created" if phase == "end" else "pending")
                )
                if child_status != "unknown":
                    child.status = child_status
                if phase == "end" and child_status in _SUCCESS | {"created"}:
                    child.created = True
                    child.failed = False
                if child_status in _FAILURE and not child.created:
                    child.failed = True

        # A direct runtime model observation for a child may arrive in a
        # session metadata object.  Accept only explicit child/thread linkage;
        # a configured session model remains out of scope.
        for item in _json_dicts(event):
            thread_id = _first(item, "child_thread_id", "agent_thread_id")
            model, source = _model_from(item)
            if isinstance(thread_id, str) and thread_id and model and source == "runtime":
                child = children.setdefault(thread_id, _Child(thread_id=thread_id))
                child.model = model
                child.model_source = "runtime"

    # If no explicit root metadata exists, a parent id from a spawn event is
    # still runtime evidence of the root thread.  A sole usage thread is also
    # a safe root inference for a no-child run.
    if not root_ids:
        parent_candidates = {
            attempt.parent_thread_id
            for attempt in attempts.values()
            if attempt.parent_thread_id
        }
        root_ids.update(parent_candidates)

    # A child rollout can carry its own session_meta.  Once the spawn edge is
    # known, that session id is a child thread rather than a second root.
    root_ids.difference_update(children)

    for thread_hint, model, effort, kind in runtime_identity_candidates:
        if kind in {
            "collab_agent_spawn_begin",
            "collab_agent_spawn_end",
            "collab_agent_spawned",
            "child_created",
            "spawned",
        }:
            continue
        if thread_hint and thread_hint in children:
            continue
        if thread_hint and root_ids and thread_hint not in root_ids:
            continue
        if model:
            parent_models.add(model)
        if effort:
            parent_efforts.add(effort)

    if len(parent_models) > 1:
        errors.append("conflicting runtime parent models")
    if len(parent_efforts) > 1:
        errors.append("conflicting runtime parent reasoning efforts")

    # A child that was successfully created still belongs in the spawn
    # denominator even when its later execution fails.  ``failed`` describes
    # the child run, whereas ``created`` is the creation evidence we measure.
    spawn_count = sum(1 for child in children.values() if child.created)
    spawn_attempts = len(attempts)

    # Lifecycle and child identity completeness are separate from usage.  A
    # child with unknown runtime model must remain unknown and blocks claims of
    # all-Luna routing.
    observation_complete = bool(root_ids and completion_marker)
    if not completion_marker:
        errors.append("missing collection-complete lifecycle marker")
    if observed_spawn_structure:
        if not attempts:
            observation_complete = False
        for attempt in attempts.values():
            if not attempt.end:
                observation_complete = False
                errors.append(f"spawn attempt {attempt.key} has no end event")
        for child in children.values():
            if child.created and not child.parent_thread_id:
                observation_complete = False
                errors.append(f"child {child.thread_id} has no parent_thread_id")
            if child.created and child.model_source != "runtime":
                observation_complete = False
                errors.append(f"child {child.thread_id} has no runtime model evidence")
    if not root_ids:
        observation_complete = False
        errors.append("no root thread/session metadata")

    records, usage_errors, _ = _usage_records(events, root_ids)
    errors.extend(usage_errors)
    parent_tokens, child_tokens, total_tokens, observed_tokens, usage_complete, aggregate_errors = _aggregate_usage(
        records, root_ids, children
    )
    errors.extend(aggregate_errors)
    if not completion_marker:
        usage_complete = False
        total_tokens = None
        errors.append("usage coverage has no collection-complete marker")

    if len(parent_models) > 1 or len(parent_efforts) > 1:
        observation_complete = False

    identity_complete = all(
        (not child.created)
        or (
            child.parent_thread_id
            and child.model_source == "runtime"
        )
        for child in children.values()
    )

    def child_dict(child: _Child) -> dict[str, Any]:
        return {
            "thread_id": child.thread_id,
            "parent_thread_id": child.parent_thread_id,
            "model": child.model,
            "model_source": child.model_source,
            "status": child.status,
        }

    unique_errors = list(dict.fromkeys(errors))
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "spawn_attempts": spawn_attempts,
        "spawn_count": spawn_count,
        "children": [child_dict(children[key]) for key in sorted(children)],
        "observation_complete": bool(observation_complete),
        "identity_complete": bool(identity_complete),
        "usage_complete": bool(usage_complete),
        "parent_model": next(iter(parent_models), None)
        if len(parent_models) == 1
        else None,
        "parent_model_source": "runtime" if len(parent_models) == 1 else "unknown",
        "parent_effort": next(iter(parent_efforts), None)
        if len(parent_efforts) == 1
        else None,
        "parent_tokens": parent_tokens,
        "child_tokens": child_tokens,
        "total_tokens": total_tokens,
        "observed_tokens": observed_tokens,
        "usd_estimate": None,
        "errors": unique_errors,
    }


__all__ = ["EVENT_SCHEMA_VERSION", "collect_events"]

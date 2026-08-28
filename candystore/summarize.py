from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

# The `type` column holds two shapes forever: the historical five-token
# `bloodbank.v1.<domain>.<entity>.<action>` on the ~713k rows already in the
# table, and the current version-free four-token
# `bloodbank.<domain>.<entity>.<action>` on everything published since. Rather
# than key SUMMARIZERS on both spellings of every type -- which doubles the
# table and guarantees the next entry only gets one half -- normalize the
# incoming type ONCE here and key the table on the canonical shape alone.
_VERSION_TOKEN = re.compile(r"^bloodbank\.v[0-9]+\.")

# Renames that survive version-stripping: the entity itself moved. The
# `tool.tool_call.*` lane became `agent.tool.*` with a byte-identical payload
# (verified against live rows: both carry tool_name/tool_call_id/arguments/
# invocation_id/turn_number). Note `tool.tool_call.completed` never existed on
# the bus -- that lane only ever emitted `requested` and `invoked`.
_RENAMED_TYPES = {
    "bloodbank.tool.tool_call.requested": "bloodbank.agent.tool.requested",
    "bloodbank.tool.tool_call.invoked": "bloodbank.agent.tool.invoked",
    "bloodbank.tool.tool_call.completed": "bloodbank.agent.tool.completed",
}


def canonical_type(event_type: Any) -> str:
    """Collapse either wire shape onto the canonical version-free type."""
    if not isinstance(event_type, str):
        return ""
    stripped = _VERSION_TOKEN.sub("bloodbank.", event_type, count=1)
    return _RENAMED_TYPES.get(stripped, stripped)


def summarize(env: dict[str, Any]) -> dict[str, Any]:
    fn = SUMMARIZERS.get(canonical_type(env.get("type", "")), _generic)
    return fn(env)


def _fmt_duration(seconds: Any) -> str:
    if seconds is None:
        return "unknown"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "unknown"

    hours, rem = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _preview(value: Any, limit: int = 200) -> str | None:
    """One-line, length-bounded rendering of an arbitrary payload field."""
    if value is None or value == "" or value == {} or value == []:
        return None
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            value = str(value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "\u2026"


def _session_ended(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    actor = env.get("actor") or {}
    project = _project_from_data(data)
    cli = actor.get("cli")
    return {
        "title": f"Session ended - {project} ({cli or 'unknown'})",
        "cli": cli,
        "provider": actor.get("provider"),
        "project": project,
        "duration": _fmt_duration(data.get("duration_seconds")),
        "turns": data.get("total_turns"),
        "tools_used": len(data.get("tools_used") or []),
        "files_modified": data.get("files_modified"),
        "git_commits": data.get("git_commits"),
        "final_status": data.get("final_status"),
        "end_reason": data.get("end_reason"),
        "working_directory": data.get("working_directory"),
    }


def _session_started(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    actor = env.get("actor") or {}
    project = _project_from_data(data)
    return {
        "title": f"Session started - {project}",
        "cli": actor.get("cli"),
        "project": project,
        "working_directory": data.get("working_directory"),
        "git_branch": data.get("git_branch"),
        "git_remote": data.get("git_remote"),
    }


def _turn_index(data: dict[str, Any]) -> Any:
    """turn_index if the producer sent one, else the `<thread>:<n>` suffix."""
    if data.get("turn_index") is not None:
        return data["turn_index"]
    turn_id = data.get("turn_id")
    if isinstance(turn_id, str) and ":" in turn_id:
        tail = turn_id.rsplit(":", 1)[1]
        if tail.isdigit():
            return int(tail)
    return None


def _turn_started(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    index = _turn_index(data)
    return {
        "title": f"Turn started - {index if index is not None else 'unknown'}",
        "session_id": data.get("session_id") or data.get("thread_id"),
        "turn_index": index,
        "model": data.get("model"),
        "prompt_preview": _preview(data.get("prompt_text")),
    }


def _tool_status(data: dict[str, Any]) -> Any:
    """`status` is legacy; live rows carry `outcome`, or a `success` bool."""
    for key in ("status", "outcome"):
        if data.get(key) is not None:
            return data[key]
    success = data.get("success")
    if isinstance(success, bool):
        return "success" if success else "failure"
    return None


def _tool_event(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    tool = data.get("tool_name") or data.get("name") or "tool"
    return {
        "title": f"{tool} - {_verb_from_type(env.get('type', ''))}",
        "tool": tool,
        "status": _tool_status(data),
        "duration": _fmt_duration(data.get("duration_seconds")),
        "input_preview": data.get("input_preview") or _preview(data.get("arguments")),
    }


def _agent_event(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    return {
        "title": f"Agent invocation - {_verb_from_type(env.get('type', ''))}",
        "agent": (
            data.get("agent")
            or data.get("agent_name")
            or data.get("source_agent_id")
        ),
        "status": data.get("status") or data.get("stop_reason"),
        "duration": _fmt_duration(data.get("duration_seconds")),
        "error": _preview(data.get("error") or data.get("error_message")),
        "invocation_id": data.get("invocation_id"),
    }


def _heartbeat(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    # `source_id` is the schema field name; `producer_id` is the legacy one.
    source = data.get("producer_id") or data.get("source_id") or env.get("producer")
    return {
        "title": f"Heartbeat - {source or 'unknown'}",
        # `or` would mask a legitimate sequence 0, so probe explicitly.
        "tick_seq": (
            data["tick_seq"] if data.get("tick_seq") is not None else data.get("sequence")
        ),
        "producer_id": data.get("producer_id") or data.get("source_id"),
        "service": env.get("service"),
    }


def _generic(env: dict[str, Any]) -> dict[str, Any]:
    actor = env.get("actor") or {}
    data = env.get("data") or {}
    return {
        "title": env.get("type", "unknown event"),
        "producer": env.get("producer"),
        "service": env.get("service"),
        "domain": env.get("domain"),
        "cli": actor.get("cli"),
        "project": _project_from_data(data),
    }


def _project_from_data(data: dict[str, Any]) -> str:
    if data.get("project"):
        return str(data["project"])
    if data.get("git_remote"):
        return str(data["git_remote"]).rstrip("/").split("/")[-1].replace(".git", "")
    if data.get("working_directory"):
        return str(data["working_directory"]).rstrip("/").split("/")[-1]
    return "unknown"


def _verb_from_type(event_type: str) -> str:
    return event_type.rsplit(".", 1)[-1].replace("_", " ") if event_type else "event"


# Keys are CANONICAL types only -- summarize() normalizes before the lookup,
# so each entry covers both the historical `bloodbank.v1.*` rows and the
# current version-free ones. `agent.session.*` supersedes `cli.session.*` for
# agent CLIs (see schemas/bloodbank/agent/session.ended.json) but both still
# have schemas and both are in the table, so both are listed.
SUMMARIZERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "bloodbank.agent.session.started": _session_started,
    "bloodbank.agent.session.ended": _session_ended,
    "bloodbank.cli.session.started": _session_started,
    "bloodbank.cli.session.ended": _session_ended,
    "bloodbank.conversation.turn.started": _turn_started,
    "bloodbank.agent.tool.requested": _tool_event,
    "bloodbank.agent.tool.invoked": _tool_event,
    "bloodbank.agent.tool.completed": _tool_event,
    "bloodbank.agent.invocation.started": _agent_event,
    "bloodbank.agent.invocation.completed": _agent_event,
    "bloodbank.agent.invocation.failed": _agent_event,
    "bloodbank.system.heartbeat.received": _heartbeat,
    # Pre-Bloodbank raw heartbeat spelling; has no version token to strip.
    "system.heartbeat.tick": _heartbeat,
}

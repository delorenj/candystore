from __future__ import annotations

from collections.abc import Callable
from typing import Any


def summarize(env: dict[str, Any]) -> dict[str, Any]:
    fn = SUMMARIZERS.get(env.get("type", ""), _generic)
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


def _turn_started(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    return {
        "title": f"Turn started - {data.get('turn_index', 'unknown')}",
        "session_id": data.get("session_id"),
        "turn_index": data.get("turn_index"),
        "model": data.get("model"),
    }


def _tool_event(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    tool = data.get("tool_name") or data.get("name") or "tool"
    return {
        "title": f"{tool} - {_verb_from_type(env.get('type', ''))}",
        "tool": tool,
        "status": data.get("status"),
        "duration": _fmt_duration(data.get("duration_seconds")),
        "input_preview": data.get("input_preview"),
    }


def _agent_event(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    return {
        "title": f"Agent invocation - {_verb_from_type(env.get('type', ''))}",
        "agent": data.get("agent") or data.get("agent_name"),
        "status": data.get("status"),
        "duration": _fmt_duration(data.get("duration_seconds")),
        "error": data.get("error"),
    }


def _heartbeat(env: dict[str, Any]) -> dict[str, Any]:
    data = env.get("data") or {}
    return {
        "title": f"Heartbeat - {data.get('producer_id', env.get('producer', 'unknown'))}",
        "tick_seq": data.get("tick_seq"),
        "producer_id": data.get("producer_id"),
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


SUMMARIZERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "bloodbank.v1.cli.session.ended": _session_ended,
    "bloodbank.v1.cli.session.started": _session_started,
    "bloodbank.v1.conversation.turn.started": _turn_started,
    "bloodbank.v1.tool.tool_call.requested": _tool_event,
    "bloodbank.v1.tool.tool_call.invoked": _tool_event,
    "bloodbank.v1.tool.tool_call.completed": _tool_event,
    "bloodbank.v1.agent.invocation.completed": _agent_event,
    "bloodbank.v1.agent.invocation.failed": _agent_event,
    "bloodbank.v1.system.heartbeat.received": _heartbeat,
    "system.heartbeat.tick": _heartbeat,
}

"""Pure reconstruction of session state from versioned event dictionaries."""

from __future__ import annotations


def project_events(events: list[dict]) -> dict:
    start = next((event for event in events if event.get("type") == "start"), None)
    if start is None or start.get("v") not in (1, 2):
        raise ValueError("unsupported or missing session schema")

    state = {
        "model": start.get("model", ""),
        "workspace": start.get("workspace", ""),
        "messages": [],
        "plan_snapshot": None,
        "created": start.get("ts"),
        "updated": events[-1].get("ts"),
        "seq": events[-1].get("seq", 0),
        "pending_review": None,
        "incomplete_calls": {},
        "finished_calls": {},
        "benchmark_state": {},
        "last_status": "complete",
        "turn_requests": 0,
        "turn_tool_rounds": 0,
        "attachments": [],
        "background_tasks": {},
        "subagents": {},
        "usage": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
        "usage_by_model": {},
        "routing_events": [],
        "active_mcp_tools": [],
        "active_task_hash": None,
        "task_candidate": None,
        "task_candidate_index": 0,
        "task_start_index": 0,
        "task_boundary_pending": False,
        "processes": {},
        "skill_snapshot": None,
    }
    for event in events:
        kind = event.get("type")
        if kind == "checkpoint":
            state["messages"] = list(event.get("messages", []))
            state["task_start_index"] = 0
            state["task_boundary_pending"] = False
        elif kind == "message":
            state["messages"].append(event["message"])
        elif kind == "plan":
            state["plan_snapshot"] = event["plan"]
        elif kind == "skills_snapshotted":
            state["skill_snapshot"] = list(event["entries"])
        elif kind == "model_changed":
            state["model"] = event["model"]
        elif kind == "workspace_changed":
            state["workspace"] = event["workspace"]
        elif kind == "review_requested":
            state["pending_review"] = event["review"]
        elif kind == "review_resolved":
            state["pending_review"] = None
        elif kind == "tool_started":
            state["incomplete_calls"][event["call"]["id"]] = event["call"]
        elif kind == "tool_finished":
            state["incomplete_calls"].pop(event["call_id"], None)
            state["finished_calls"][event["call_id"]] = event
        elif kind == "benchmark_state":
            state["benchmark_state"] = dict(event["state"])
        elif kind == "turn_status":
            state["last_status"] = event["status"]
        elif kind == "turn_started":
            state["turn_requests"] = 0
            state["turn_tool_rounds"] = 0
            state["active_mcp_tools"] = []
        elif kind == "mcp_tools_activated":
            state["active_mcp_tools"].extend(event["names"])
        elif kind == "task_boundary_observed":
            state["active_task_hash"] = event["active_hash"]
            state["task_candidate"] = event["candidate_hash"]
            state["task_candidate_index"] = event["candidate_index"]
            state["task_start_index"] = event["task_start_index"]
            if event.get("confirmed_change"):
                state["task_boundary_pending"] = True
        elif kind == "task_boundary_acknowledged":
            state["task_boundary_pending"] = False
        elif kind == "model_request":
            state["turn_requests"] += 1
        elif kind == "tool_round":
            state["turn_tool_rounds"] += 1
        elif kind == "attachment":
            state["attachments"].append(event["attachment"])
        elif kind == "background_started":
            task = event["task"]
            state["background_tasks"][task["id"]] = dict(task)
        elif kind == "background_finished":
            state["background_tasks"][event["task_id"]].update(status=event["status"], result=event["result"])
        elif kind == "background_notified":
            state["background_tasks"][event["task_id"]]["notified"] = True
            if event.get("message"):
                state["messages"].append({"role": "user", "content": event["message"], "synthetic": True})
        elif kind == "subagent_created":
            child = event["child"]
            state["subagents"][child["id"]] = dict(child)
        elif kind == "subagent_applied":
            state["subagents"][event["child_id"]]["applied"] = True
        elif kind == "process_started":
            process = event["process"]
            state["processes"][process["id"]] = dict(process)
        elif kind == "process_finished":
            state["processes"][event["process_id"]]["status"] = event["status"]
        elif kind == "usage":
            state["usage"]["requests"] += 1
            state["usage"]["input_tokens"] += event.get("input_tokens", 0)
            state["usage"]["output_tokens"] += event.get("output_tokens", 0)
            key = (f'{event["provider"]}/{event["model"]}' if event.get("provider") and event.get("model")
                   else "unknown")
            row = state["usage_by_model"].setdefault(
                key, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
            row["requests"] += 1
            row["input_tokens"] += event.get("input_tokens", 0)
            row["output_tokens"] += event.get("output_tokens", 0)
        elif kind == "routing_decision":
            state["routing_events"].append(event)

    for task in state["background_tasks"].values():
        if task["status"] == "running":
            task["status"] = "interrupted"
    for process in state["processes"].values():
        if process["status"] == "running":
            process["status"] = "detached"
    return state

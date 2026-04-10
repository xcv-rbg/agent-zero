import json
import os
import time

from helpers.api import ApiHandler, Request, Response

STORAGE_KEY = "_warroom_todo"
VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}
VALID_PRIORITIES = {"low", "normal", "high", "critical"}


def _persist_path(context_id: str) -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "usr", "warroom_tasks")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"warroom_todo_{context_id}.json")


def _save_to_disk(context_id: str, tasks: list[dict]) -> None:
    try:
        path = _persist_path(context_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _find_task(tasks: list[dict], task_id: str) -> dict | None:
    for t in tasks:
        if t["id"] == task_id:
            return t
        for st in t.get("subtasks", []):
            if st["id"] == task_id:
                return st
    return None


class TodoUpdate(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        if not input:
            return {"ok": False, "error": "Empty request body"}

        ctxid = input.get("context", "").strip()
        if not ctxid:
            return {"ok": False, "error": "Missing required field: 'context'"}

        task_id = input.get("task_id", "").strip()
        if not task_id:
            return {"ok": False, "error": "Missing required field: 'task_id'"}

        ctx = self.use_context(ctxid, create_if_not_exists=False)
        if ctx is None:
            return {"ok": False, "error": f"Context '{ctxid}' not found"}

        tasks: list[dict] = ctx.data.get(STORAGE_KEY, [])
        task = _find_task(tasks, task_id)
        if task is None:
            return {"ok": False, "error": f"Task '{task_id}' not found"}

        changed: list[str] = []
        for field in ("title", "description", "status", "priority"):
            val = input.get(field)
            if val is None:
                continue
            if field == "status":
                if val not in VALID_STATUSES:
                    return {"ok": False, "error": f"Invalid status '{val}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"}
            if field == "priority":
                if val not in VALID_PRIORITIES:
                    return {"ok": False, "error": f"Invalid priority '{val}'. Must be one of: {', '.join(sorted(VALID_PRIORITIES))}"}
            task[field] = val.strip() if isinstance(val, str) else val
            changed.append(field)

        if not changed:
            return {"ok": False, "error": "No updatable fields provided. Supply title, description, status, or priority."}

        task["updated_at"] = time.time()
        ctx.data[STORAGE_KEY] = tasks
        _save_to_disk(ctxid, tasks)

        return {"ok": True, "task": task}

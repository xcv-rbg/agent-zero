import json
import os
import time
import uuid

from helpers.api import ApiHandler, Request, Response

STORAGE_KEY = "_warroom_todo"
MAX_TASKS = 50
MAX_SUBTASKS = 10
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


def _reindex(tasks: list[dict]) -> None:
    for idx, t in enumerate(tasks, start=1):
        t["order"] = idx
        for si, st in enumerate(t.get("subtasks", []), start=1):
            st["order"] = si


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class TodoAdd(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        if not input:
            return {"ok": False, "error": "Empty request body"}

        ctxid = input.get("context", "").strip()
        if not ctxid:
            return {"ok": False, "error": "Missing required field: 'context'"}

        title = input.get("title", "").strip()
        if not title:
            return {"ok": False, "error": "Missing required field: 'title'"}

        description = input.get("description", "").strip() if input.get("description") else ""
        priority = input.get("priority", "normal").strip()
        if priority not in VALID_PRIORITIES:
            return {"ok": False, "error": f"Invalid priority '{priority}'. Must be one of: {', '.join(sorted(VALID_PRIORITIES))}"}

        parent_id = input.get("parent_id", "").strip() if input.get("parent_id") else ""

        ctx = self.use_context(ctxid, create_if_not_exists=False)
        if ctx is None:
            return {"ok": False, "error": f"Context '{ctxid}' not found"}

        tasks: list[dict] = ctx.data.get(STORAGE_KEY, [])

        now = time.time()

        if parent_id:
            # Add as subtask
            parent = None
            for t in tasks:
                if t["id"] == parent_id:
                    parent = t
                    break
            if parent is None:
                return {"ok": False, "error": f"Parent task '{parent_id}' not found"}

            existing_subtasks = parent.get("subtasks", [])
            if len(existing_subtasks) >= MAX_SUBTASKS:
                return {"ok": False, "error": f"Maximum of {MAX_SUBTASKS} subtasks reached for this task"}

            subtask = {
                "id": _new_id(),
                "order": len(existing_subtasks) + 1,
                "title": title,
                "description": description,
                "status": "pending",
                "priority": priority,
                "created_by": "superuser",
                "created_at": now,
                "updated_at": now,
            }
            parent.setdefault("subtasks", []).append(subtask)
            _reindex(tasks)
            ctx.data[STORAGE_KEY] = tasks
            _save_to_disk(ctxid, tasks)
            return {"ok": True, "task": subtask}
        else:
            # Add as top-level task
            if len(tasks) >= MAX_TASKS:
                return {"ok": False, "error": f"Maximum of {MAX_TASKS} tasks reached"}

            task = {
                "id": _new_id(),
                "order": len(tasks) + 1,
                "title": title,
                "description": description,
                "status": "pending",
                "priority": priority,
                "created_by": "superuser",
                "created_at": now,
                "updated_at": now,
                "subtasks": [],
            }
            tasks.append(task)
            _reindex(tasks)
            ctx.data[STORAGE_KEY] = tasks
            _save_to_disk(ctxid, tasks)
            return {"ok": True, "task": task}

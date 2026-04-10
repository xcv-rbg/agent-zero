import json
import os

from helpers.api import ApiHandler, Request, Response

STORAGE_KEY = "_warroom_todo"


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


class TodoRemove(ApiHandler):
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

        # Try removing as top-level task
        for i, t in enumerate(tasks):
            if t["id"] == task_id:
                tasks.pop(i)
                _reindex(tasks)
                ctx.data[STORAGE_KEY] = tasks
                _save_to_disk(ctxid, tasks)
                return {"ok": True, "removed_id": task_id}

        # Try removing as subtask
        for t in tasks:
            subtasks = t.get("subtasks", [])
            for j, st in enumerate(subtasks):
                if st["id"] == task_id:
                    subtasks.pop(j)
                    _reindex(tasks)
                    ctx.data[STORAGE_KEY] = tasks
                    _save_to_disk(ctxid, tasks)
                    return {"ok": True, "removed_id": task_id}

        return {"ok": False, "error": f"Task '{task_id}' not found"}

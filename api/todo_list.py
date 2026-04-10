import json
import os

from helpers.api import ApiHandler, Request, Response

STORAGE_KEY = "_warroom_todo"


def _persist_path(context_id: str) -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "usr", "warroom_tasks")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"warroom_todo_{context_id}.json")


def _load_from_disk(context_id: str) -> list[dict] | None:
    try:
        path = _persist_path(context_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return None


def _compute_progress(tasks: list[dict]) -> dict:
    """Compute progress for top-level tasks only (subtasks tracked separately)."""
    total = pending = in_progress = done = blocked = 0
    sub_total = sub_done = 0
    for t in tasks:
        total += 1
        s = t.get("status", "pending")
        if s == "pending":
            pending += 1
        elif s == "in_progress":
            in_progress += 1
        elif s == "done":
            done += 1
        elif s == "blocked":
            blocked += 1
        for st in t.get("subtasks", []):
            sub_total += 1
            if st.get("status") == "done":
                sub_done += 1
    return {
        "total": total,
        "pending": pending,
        "in_progress": in_progress,
        "done": done,
        "blocked": blocked,
        "sub_total": sub_total,
        "sub_done": sub_done,
    }


class TodoList(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        ctxid = input.get("context", "").strip() if input else ""
        if not ctxid:
            return {"ok": False, "error": "Missing required field: 'context'"}

        # Try live context first, fall back to disk
        tasks: list[dict] = []
        try:
            ctx = self.use_context(ctxid, create_if_not_exists=False)
            if ctx is not None:
                tasks = ctx.data.get(STORAGE_KEY, [])
        except Exception:
            pass

        if not tasks:
            disk_tasks = _load_from_disk(ctxid)
            if disk_tasks is not None:
                tasks = disk_tasks

        progress = _compute_progress(tasks)
        return {"ok": True, "tasks": tasks, "progress": progress}

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

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


class TodoReorder(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        if not input:
            return {"ok": False, "error": "Empty request body"}

        ctxid = input.get("context", "").strip()
        if not ctxid:
            return {"ok": False, "error": "Missing required field: 'context'"}

        task_ids = input.get("task_ids")
        if not task_ids or not isinstance(task_ids, list):
            return {"ok": False, "error": "Missing or invalid 'task_ids': expected a list of task ID strings"}

        ctx = self.use_context(ctxid, create_if_not_exists=False)
        if ctx is None:
            return {"ok": False, "error": f"Context '{ctxid}' not found"}

        tasks: list[dict] = ctx.data.get(STORAGE_KEY, [])
        task_map = {t["id"]: t for t in tasks}

        # Check if these are top-level task IDs
        if all(tid in task_map for tid in task_ids):
            if set(task_ids) != set(task_map.keys()):
                return {"ok": False, "error": "task_ids must contain exactly all top-level task IDs"}
            reordered = [task_map[tid] for tid in task_ids]
            _reindex(reordered)
            ctx.data[STORAGE_KEY] = reordered
            _save_to_disk(ctxid, reordered)
            return {"ok": True}

        # Check if these are subtask IDs within a single parent
        parent = None
        for t in tasks:
            subtask_map = {st["id"]: st for st in t.get("subtasks", [])}
            if all(tid in subtask_map for tid in task_ids):
                if set(task_ids) != set(subtask_map.keys()):
                    return {"ok": False, "error": "task_ids must contain exactly all subtask IDs of the parent task"}
                parent = t
                reordered_subs = [subtask_map[tid] for tid in task_ids]
                parent["subtasks"] = reordered_subs
                _reindex(tasks)
                ctx.data[STORAGE_KEY] = tasks
                _save_to_disk(ctxid, tasks)
                return {"ok": True}

        return {"ok": False, "error": "task_ids do not match any complete set of top-level tasks or subtasks within a single parent"}

"""War Room Todo Tool — structured task tracker for Agent Zero contexts.

Provides ordered, nested task lists with permission-gated CRUD,
duplicate detection, crash-recovery persistence, and rich display formatting.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from difflib import SequenceMatcher
from typing import Any

from helpers.tool import Tool, Response


# ── constants ────────────────────────────────────────────────────────────────

STORAGE_KEY = "_warroom_todo"
MAX_TASKS = 50
MAX_SUBTASKS = 10
DUPLICATE_THRESHOLD = 0.80

VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}
VALID_PRIORITIES = {"low", "normal", "high", "critical"}
PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}

WRITE_CALLERS = {"think", "main_agent", "utility"}
DELETE_CALLERS = {"superuser"}

STATUS_ICONS = {
    "done": "✅",
    "in_progress": "🔄",
    "pending": "⏳",
    "blocked": "🚫",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _similarity(a: str, b: str) -> float:
    """Return 0-1 ratio of similarity between two strings (case-insensitive)."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _make_task(
    title: str,
    description: str = "",
    priority: str = "normal",
    caller: str = "main_agent",
    order: int = 1,
    subtasks: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a well-formed task dict."""
    now = _now()
    task: dict[str, Any] = {
        "id": _new_id(),
        "order": order,
        "title": title.strip(),
        "description": description.strip(),
        "status": "pending",
        "priority": priority if priority in VALID_PRIORITIES else "normal",
        "created_by": caller,
        "created_at": now,
        "updated_at": now,
        "subtasks": [],
    }
    if subtasks:
        for idx, st in enumerate(subtasks[:MAX_SUBTASKS], start=1):
            task["subtasks"].append(
                _make_subtask(
                    title=st.get("title", ""),
                    description=st.get("description", ""),
                    priority=st.get("priority", "normal"),
                    caller=caller,
                    order=idx,
                )
            )
    return task


def _make_subtask(
    title: str,
    description: str = "",
    priority: str = "normal",
    caller: str = "main_agent",
    order: int = 1,
) -> dict[str, Any]:
    now = _now()
    return {
        "id": _new_id(),
        "order": order,
        "title": title.strip(),
        "description": description.strip(),
        "status": "pending",
        "priority": priority if priority in VALID_PRIORITIES else "normal",
        "created_by": caller,
        "created_at": now,
        "updated_at": now,
    }


def _reindex(tasks: list[dict]) -> None:
    """Reassign sequential order numbers starting at 1."""
    for idx, t in enumerate(tasks, start=1):
        t["order"] = idx
        for si, st in enumerate(t.get("subtasks", []), start=1):
            st["order"] = si


def _find_task(tasks: list[dict], task_id: str) -> dict | None:
    for t in tasks:
        if t["id"] == task_id:
            return t
        for st in t.get("subtasks", []):
            if st["id"] == task_id:
                return st
    return None


def _find_parent(tasks: list[dict], subtask_id: str) -> dict | None:
    for t in tasks:
        for st in t.get("subtasks", []):
            if st["id"] == subtask_id:
                return t
    return None


def _persist_path(context_id: str) -> str:
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "usr", "warroom_tasks")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"warroom_todo_{context_id}.json")


# ── tool class ───────────────────────────────────────────────────────────────

class Todo(Tool):
    """Structured todo list for War Room contexts.

    Supports add / update / list / get / remove / reorder / add_subtask /
    bulk_add / next / progress methods with role-based permissions.
    """

    _lock = threading.RLock()

    # ── dispatch ─────────────────────────────────────────────────────────

    async def execute(self, **kwargs) -> Response:
        method = (self.method or self.args.get("method", "list")).lower()
        dispatch = {
            "add": self._add,
            "update": self._update,
            "list": self._list,
            "get": self._get,
            "remove": self._remove,
            "reorder": self._reorder,
            "add_subtask": self._add_subtask,
            "bulk_add": self._bulk_add,
            "next": self._next,
            "progress": self._progress,
        }
        handler = dispatch.get(method)
        if handler is None:
            return Response(
                message=f"Unknown todo method '{method}'. Available: {', '.join(sorted(dispatch))}",
                break_loop=False,
            )
        return await handler()

    # ── storage ──────────────────────────────────────────────────────────

    def _get_tasks(self) -> list[dict]:
        """Return the live tasks list from context data, recovering from disk if needed."""
        data = self.agent.context.data
        if STORAGE_KEY not in data:
            recovered = self._load_from_disk()
            data[STORAGE_KEY] = recovered if recovered is not None else []
        return data[STORAGE_KEY]

    def _save(self, tasks: list[dict]) -> None:
        """Persist tasks to context data and to disk."""
        self.agent.context.data[STORAGE_KEY] = tasks
        self._save_to_disk(tasks)

    def _save_to_disk(self, tasks: list[dict]) -> None:
        try:
            path = _persist_path(self.agent.context.id)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # best-effort persistence

    def _load_from_disk(self) -> list[dict] | None:
        try:
            path = _persist_path(self.agent.context.id)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return None

    # ── permission helpers ───────────────────────────────────────────────

    def _caller(self) -> str:
        return self.args.get("_caller", "main_agent")

    def _require_write(self) -> str | None:
        """Return an error message if the caller lacks write permission."""
        caller = self._caller()
        if caller not in WRITE_CALLERS and caller not in DELETE_CALLERS:
            return f"Permission denied: caller '{caller}' cannot write to the todo list."
        return None

    def _require_delete(self) -> str | None:
        caller = self._caller()
        if caller not in DELETE_CALLERS:
            return f"Permission denied: only superuser can remove tasks (caller='{caller}')."
        return None

    # ── duplicate detection ──────────────────────────────────────────────

    def _find_duplicate(self, tasks: list[dict], title: str) -> dict | None:
        """Return an existing task whose title is suspiciously similar."""
        for t in tasks:
            if _similarity(t["title"], title) >= DUPLICATE_THRESHOLD:
                return t
            for st in t.get("subtasks", []):
                if _similarity(st["title"], title) >= DUPLICATE_THRESHOLD:
                    return st
        return None

    # ── method implementations ───────────────────────────────────────────

    async def _add(self) -> Response:
        perm_err = self._require_write()
        if perm_err:
            return Response(message=perm_err, break_loop=False)

        with self._lock:
            tasks = self._get_tasks()

            # accept either a single title or a tasks list
            task_list = self.args.get("tasks")
            if task_list and isinstance(task_list, list):
                return await self._do_bulk_add(tasks, task_list)

            title = self.args.get("title", "").strip()
            if not title:
                return Response(message="Error: 'title' is required to add a task.", break_loop=False)

            if len(tasks) >= MAX_TASKS:
                return Response(message=f"Error: maximum of {MAX_TASKS} tasks reached.", break_loop=False)

            dup = self._find_duplicate(tasks, title)
            if dup:
                return Response(
                    message=f"Warning: a similar task already exists — \"{dup['title']}\" (id={dup['id']}, status={dup['status']}). Not added.",
                    break_loop=False,
                )

            subtasks_raw = self.args.get("subtasks")
            subtasks = subtasks_raw if isinstance(subtasks_raw, list) else None
            task = _make_task(
                title=title,
                description=self.args.get("description", ""),
                priority=self.args.get("priority", "normal"),
                caller=self._caller(),
                order=len(tasks) + 1,
                subtasks=subtasks,
            )
            tasks.append(task)
            _reindex(tasks)
            self._save(tasks)

        return Response(
            message=f"Task added: \"{task['title']}\" (id={task['id']}, priority={task['priority']}, subtasks={len(task['subtasks'])})",
            break_loop=False,
        )

    async def _update(self) -> Response:
        perm_err = self._require_write()
        if perm_err:
            return Response(message=perm_err, break_loop=False)

        task_id = self.args.get("task_id", "").strip()
        if not task_id:
            return Response(message="Error: 'task_id' is required.", break_loop=False)

        with self._lock:
            tasks = self._get_tasks()
            task = _find_task(tasks, task_id)
            if task is None:
                return Response(message=f"Error: task '{task_id}' not found.", break_loop=False)

            changed: list[str] = []
            for field in ("title", "description", "status", "priority"):
                val = self.args.get(field)
                if val is None:
                    continue
                if field == "status" and val not in VALID_STATUSES:
                    return Response(
                        message=f"Error: invalid status '{val}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
                        break_loop=False,
                    )
                if field == "priority" and val not in VALID_PRIORITIES:
                    return Response(
                        message=f"Error: invalid priority '{val}'. Must be one of: {', '.join(sorted(VALID_PRIORITIES))}",
                        break_loop=False,
                    )
                task[field] = val.strip() if isinstance(val, str) else val
                changed.append(field)

            if not changed:
                return Response(message="No fields to update. Supply title, description, status, or priority.", break_loop=False)

            task["updated_at"] = _now()
            self._save(tasks)

            # check if parent should be marked done
            suggestion = ""
            parent = _find_parent(tasks, task_id)
            if parent and all(st.get("status") == "done" for st in parent.get("subtasks", [])):
                suggestion = f"\n💡 All subtasks of \"{parent['title']}\" (id={parent['id']}) are done. Consider marking the parent as done."

        return Response(
            message=f"Task '{task_id}' updated: {', '.join(changed)}.{suggestion}",
            break_loop=False,
        )

    async def _list(self) -> Response:
        with self._lock:
            tasks = self._get_tasks()

        status_filter = self.args.get("status")
        fmt = self.args.get("format", "compact")

        if status_filter and status_filter in VALID_STATUSES:
            visible = [t for t in tasks if t["status"] == status_filter]
        else:
            visible = tasks

        if not visible:
            return Response(message="📋 Todo list is empty.", break_loop=False)

        done_count = sum(1 for t in tasks if t["status"] == "done")
        total = len(tasks)
        header = f"📋 War Room Todo List ({done_count}/{total} complete)"
        sep = "━" * 40

        lines = [header, sep, ""]
        for t in visible:
            icon = STATUS_ICONS.get(t["status"], "❓")
            pri = f" ({t['priority'].upper()})" if t["priority"] not in ("normal",) else ""
            if fmt == "full":
                lines.append(f"{t['order']}. [{icon}] {t['title']}{pri}  (id={t['id']})")
                if t.get("description"):
                    lines.append(f"       {t['description']}")
            else:
                lines.append(f"{t['order']}. [{icon}] {t['title']}{pri}")

            for st in t.get("subtasks", []):
                si = STATUS_ICONS.get(st["status"], "❓")
                spri = f" ({st['priority'].upper()})" if st["priority"] not in ("normal",) else ""
                if fmt == "full":
                    lines.append(f"   {t['order']}.{st['order']} [{si}] {st['title']}{spri}  (id={st['id']})")
                    if st.get("description"):
                        lines.append(f"          {st['description']}")
                else:
                    lines.append(f"   {t['order']}.{st['order']} [{si}] {st['title']}{spri}")

        lines.append("")
        lines.append("Legend: ✅=done 🔄=in_progress ⏳=pending 🚫=blocked")
        return Response(message="\n".join(lines), break_loop=False)

    async def _get(self) -> Response:
        task_id = self.args.get("task_id", "").strip()
        if not task_id:
            return Response(message="Error: 'task_id' is required.", break_loop=False)

        with self._lock:
            tasks = self._get_tasks()
            task = _find_task(tasks, task_id)

        if task is None:
            return Response(message=f"Error: task '{task_id}' not found.", break_loop=False)

        lines = [
            f"Task: {task['title']}",
            f"  ID:          {task['id']}",
            f"  Status:      {task['status']}",
            f"  Priority:    {task['priority']}",
            f"  Created by:  {task['created_by']}",
            f"  Description: {task.get('description') or '(none)'}",
        ]
        subtasks = task.get("subtasks", [])
        if subtasks:
            lines.append(f"  Subtasks ({len(subtasks)}):")
            for st in subtasks:
                icon = STATUS_ICONS.get(st["status"], "❓")
                lines.append(f"    {st['order']}. [{icon}] {st['title']} (id={st['id']}, {st['status']})")

        return Response(message="\n".join(lines), break_loop=False)

    async def _remove(self) -> Response:
        perm_err = self._require_delete()
        if perm_err:
            return Response(message=perm_err, break_loop=False)

        task_id = self.args.get("task_id", "").strip()
        if not task_id:
            return Response(message="Error: 'task_id' is required.", break_loop=False)

        with self._lock:
            tasks = self._get_tasks()

            # try top-level removal
            for i, t in enumerate(tasks):
                if t["id"] == task_id:
                    removed = tasks.pop(i)
                    _reindex(tasks)
                    self._save(tasks)
                    return Response(message=f"Task removed: \"{removed['title']}\" (id={task_id})", break_loop=False)

            # try subtask removal
            for t in tasks:
                for j, st in enumerate(t.get("subtasks", [])):
                    if st["id"] == task_id:
                        removed = t["subtasks"].pop(j)
                        _reindex(tasks)
                        t["updated_at"] = _now()
                        self._save(tasks)
                        return Response(
                            message=f"Subtask removed: \"{removed['title']}\" from parent \"{t['title']}\"",
                            break_loop=False,
                        )

        return Response(message=f"Error: task '{task_id}' not found.", break_loop=False)

    async def _reorder(self) -> Response:
        perm_err = self._require_write()
        if perm_err:
            return Response(message=perm_err, break_loop=False)

        task_ids = self.args.get("task_ids")
        if not task_ids or not isinstance(task_ids, list):
            return Response(message="Error: 'task_ids' must be a list of task ID strings.", break_loop=False)

        with self._lock:
            tasks = self._get_tasks()
            by_id = {t["id"]: t for t in tasks}
            missing = [tid for tid in task_ids if tid not in by_id]
            if missing:
                return Response(message=f"Error: unknown task IDs: {', '.join(missing)}", break_loop=False)

            # reorder: listed IDs first in given order, then remaining in original order
            reordered: list[dict] = []
            seen: set[str] = set()
            for tid in task_ids:
                if tid not in seen:
                    reordered.append(by_id[tid])
                    seen.add(tid)
            for t in tasks:
                if t["id"] not in seen:
                    reordered.append(t)

            _reindex(reordered)
            self._save(reordered)

        return Response(message=f"Tasks reordered ({len(task_ids)} repositioned).", break_loop=False)

    async def _add_subtask(self) -> Response:
        perm_err = self._require_write()
        if perm_err:
            return Response(message=perm_err, break_loop=False)

        parent_id = self.args.get("parent_id", "").strip()
        title = self.args.get("title", "").strip()
        if not parent_id:
            return Response(message="Error: 'parent_id' is required.", break_loop=False)
        if not title:
            return Response(message="Error: 'title' is required.", break_loop=False)

        with self._lock:
            tasks = self._get_tasks()
            parent = None
            for t in tasks:
                if t["id"] == parent_id:
                    parent = t
                    break
            if parent is None:
                return Response(message=f"Error: parent task '{parent_id}' not found.", break_loop=False)

            if len(parent.get("subtasks", [])) >= MAX_SUBTASKS:
                return Response(message=f"Error: parent already has {MAX_SUBTASKS} subtasks (limit).", break_loop=False)

            dup = self._find_duplicate(tasks, title)
            if dup:
                return Response(
                    message=f"Warning: a similar task already exists — \"{dup['title']}\" (id={dup['id']}). Not added.",
                    break_loop=False,
                )

            st = _make_subtask(
                title=title,
                description=self.args.get("description", ""),
                priority=self.args.get("priority", "normal"),
                caller=self._caller(),
                order=len(parent.get("subtasks", [])) + 1,
            )
            parent.setdefault("subtasks", []).append(st)
            parent["updated_at"] = _now()
            _reindex(tasks)
            self._save(tasks)

        return Response(
            message=f"Subtask added to \"{parent['title']}\": \"{st['title']}\" (id={st['id']})",
            break_loop=False,
        )

    async def _bulk_add(self) -> Response:
        perm_err = self._require_write()
        if perm_err:
            return Response(message=perm_err, break_loop=False)

        task_list = self.args.get("tasks")
        if not task_list or not isinstance(task_list, list):
            return Response(message="Error: 'tasks' must be a list of task objects.", break_loop=False)

        with self._lock:
            tasks = self._get_tasks()
            return await self._do_bulk_add(tasks, task_list)

    async def _do_bulk_add(self, tasks: list[dict], task_list: list[dict]) -> Response:
        """Shared implementation for bulk adds (called under lock)."""
        added: list[str] = []
        skipped: list[str] = []

        for item in task_list:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue
            if len(tasks) >= MAX_TASKS:
                skipped.append(f"{title} (limit reached)")
                continue

            dup = self._find_duplicate(tasks, title)
            if dup:
                skipped.append(f"{title} (duplicate of \"{dup['title']}\")")
                continue

            subtasks_raw = item.get("subtasks")
            subtasks = subtasks_raw if isinstance(subtasks_raw, list) else None
            task = _make_task(
                title=title,
                description=item.get("description", ""),
                priority=item.get("priority", "normal"),
                caller=self._caller(),
                order=len(tasks) + 1,
                subtasks=subtasks,
            )
            tasks.append(task)
            added.append(task["title"])

        _reindex(tasks)
        self._save(tasks)

        parts = [f"Added {len(added)} task(s)."]
        if added:
            parts.append("Tasks: " + "; ".join(added))
        if skipped:
            parts.append("Skipped: " + "; ".join(skipped))
        return Response(message="\n".join(parts), break_loop=False)

    async def _next(self) -> Response:
        with self._lock:
            tasks = self._get_tasks()

        # collect all pending/in_progress items (top-level only)
        candidates = [t for t in tasks if t["status"] in ("pending", "in_progress")]
        if not candidates:
            return Response(message="No pending or in-progress tasks remaining.", break_loop=False)

        # sort by: in_progress first, then priority, then order
        def sort_key(t: dict) -> tuple:
            status_rank = 0 if t["status"] == "in_progress" else 1
            return (status_rank, PRIORITY_ORDER.get(t["priority"], 2), t["order"])

        candidates.sort(key=sort_key)
        nxt = candidates[0]
        icon = STATUS_ICONS.get(nxt["status"], "❓")
        pri = f" ({nxt['priority'].upper()})" if nxt["priority"] != "normal" else ""
        subtask_info = ""
        subs = nxt.get("subtasks", [])
        if subs:
            pending_subs = [s for s in subs if s["status"] in ("pending", "in_progress")]
            if pending_subs:
                first_sub = pending_subs[0]
                subtask_info = f"\n  Next subtask: {first_sub['order']}. {first_sub['title']} (id={first_sub['id']})"

        return Response(
            message=f"Next task: [{icon}] {nxt['title']}{pri} (id={nxt['id']}){subtask_info}",
            break_loop=False,
        )

    async def _progress(self) -> Response:
        with self._lock:
            tasks = self._get_tasks()

        total = len(tasks)
        if total == 0:
            return Response(message="📊 No tasks in the todo list.", break_loop=False)

        counts = {"pending": 0, "in_progress": 0, "done": 0, "blocked": 0}
        sub_total = 0
        sub_counts = {"pending": 0, "in_progress": 0, "done": 0, "blocked": 0}

        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
            for st in t.get("subtasks", []):
                sub_total += 1
                sub_counts[st["status"]] = sub_counts.get(st["status"], 0) + 1

        pct = (counts["done"] / total * 100) if total else 0
        bar_len = 20
        filled = round(pct / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)

        lines = [
            f"📊 War Room Progress",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  [{bar}] {pct:.0f}%",
            f"",
            f"  Tasks:       {total}",
            f"    ⏳ Pending:     {counts['pending']}",
            f"    🔄 In Progress: {counts['in_progress']}",
            f"    ✅ Done:        {counts['done']}",
            f"    🚫 Blocked:     {counts['blocked']}",
        ]
        if sub_total:
            lines.append(f"")
            lines.append(f"  Subtasks:    {sub_total}")
            lines.append(f"    ⏳ Pending:     {sub_counts['pending']}")
            lines.append(f"    🔄 In Progress: {sub_counts['in_progress']}")
            lines.append(f"    ✅ Done:        {sub_counts['done']}")
            lines.append(f"    🚫 Blocked:     {sub_counts['blocked']}")

        return Response(message="\n".join(lines), break_loop=False)

# extensions/python/message_loop_prompts_before/_25_warroom_todo_compliance.py
# ─────────────────────────────────────────────────────────────────────────────
#  War Room — Todo Compliance + Focus Extension
#  Injects task-awareness context so the agent stays focused on its current
#  task and knows exactly what it should be doing.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from helpers.extension import Extension
from agent import LoopData


_FOCUS_REMINDER = (
    "\n\n---\n"
    "## 📋 WAR ROOM PLAN — CURRENT FOCUS\n"
    "**You are currently working on:** {current_task}\n"
    "{current_desc}"
    "\n**Plan progress:** {done}/{total} tasks complete.\n"
    "{remaining_summary}\n"
    "**Stay focused on the current task.** Complete it fully before moving to "
    "the next. If you encounter an error, diagnose and fix it — use `think` "
    "with `mode: fast` only if you're genuinely stuck.\n"
    "After completing the current task, mark it `done` with `todo:update` "
    "and proceed to the next pending task.\n"
    "⛔ **DO NOT use the response tool until ALL tasks are complete AND the "
    "War Room gives explicit approval.** Any premature response will be blocked.\n"
    "---\n"
)

_NO_ACTIVE_REMINDER = (
    "\n\n---\n"
    "## 📋 WAR ROOM PLAN — TASKS REMAINING\n"
    "**{pending} task(s) are still incomplete.** No task is currently in progress.\n\n"
    "{task_summary}\n\n"
    "**Pick up the next pending task** using `todo:next`, mark it `in_progress`, "
    "and execute it. Work through all tasks before responding to the user.\n"
    "⛔ **DO NOT use the response tool until ALL tasks are complete AND the "
    "War Room gives explicit approval.** Any premature response will be blocked.\n"
    "---\n"
)


class WarRoomTodoCompliance(Extension):
    """Injects focused task-awareness context into the agent's prompt."""

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent:
            return

        # Check if there's a todo list on this context
        todo_data = self.agent.context.data.get("_warroom_todo")
        if not todo_data or not isinstance(todo_data, list):
            return

        tasks = todo_data
        if not tasks:
            return

        # Categorize tasks
        active_task = None
        pending = []
        done_count = 0
        total = len(tasks)

        for t in tasks:
            status = t.get("status", "pending")
            if status == "in_progress" and not active_task:
                active_task = t
            elif status in ("pending",):
                pending.append(t)
            elif status in ("done", "skipped"):
                done_count += 1

        # All done — no injection needed
        if not active_task and not pending:
            return

        if active_task:
            # Agent is working on a task — clear the response gate directive
            # since the agent is making progress (not stuck trying to respond).
            loop_data.extras_persistent.pop("_response_gate_directive", None)

            # Build remaining tasks summary (next 3 pending)
            remaining_lines = []
            for i, t in enumerate(pending[:3], 1):
                remaining_lines.append(f"  {i}. {t.get('title', 'Untitled')}")
            remaining_summary = ""
            if remaining_lines:
                remaining_summary = "**Up next:**\n" + "\n".join(remaining_lines)
                if len(pending) > 3:
                    remaining_summary += f"\n  ... and {len(pending) - 3} more"

            desc = active_task.get("description", "")
            current_desc = f"**Details:** {desc}\n" if desc else ""

            loop_data.extras_temporary["warroom_todo_reminder"] = (
                _FOCUS_REMINDER.format(
                    current_task=active_task.get("title", "Unknown task"),
                    current_desc=current_desc,
                    done=done_count,
                    total=total,
                    remaining_summary=remaining_summary,
                )
            )
        else:
            # No active task but pending tasks remain
            lines = []
            for i, t in enumerate(pending[:5], 1):
                priority = t.get("priority", "normal")
                badge = "🔴" if priority == "high" else "🟡" if priority == "normal" else "⚪"
                lines.append(f"{badge} {i}. {t.get('title', 'Untitled')}")
            if len(pending) > 5:
                lines.append(f"   ... and {len(pending) - 5} more")

            loop_data.extras_temporary["warroom_todo_reminder"] = (
                _NO_ACTIVE_REMINDER.format(
                    pending=len(pending),
                    task_summary="\n".join(lines),
                )
            )

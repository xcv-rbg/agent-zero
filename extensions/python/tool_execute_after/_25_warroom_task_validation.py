# extensions/python/tool_execute_after/_25_warroom_task_validation.py
# ─────────────────────────────────────────────────────────────────────────────
#  War Room — Post-Task Validation Extension
#  When the agent marks a todo task as "done", this extension triggers a
#  concise War Room validation session to verify:
#    1. Was the task completed correctly?
#    2. Is there an alternative approach if this one failed?
#    3. Have we achieved the overall goal?
#    4. Should the War Room reconvene for a full re-plan?
#
#  This War Room call is UNLIMITED — let it think as much as it needs.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from helpers.extension import Extension
from helpers.tool import Response
from helpers.print_style import PrintStyle


def _get_todo_summary(agent) -> str:
    """Build a compact summary of the current todo state."""
    todo_data = agent.context.data.get("_warroom_todo")
    if not todo_data or not isinstance(todo_data, list):
        return "No todo list found."

    lines = []
    done = 0
    total = len(todo_data)
    for t in todo_data:
        status = t.get("status", "pending")
        icon = {"done": "✅", "in_progress": "🔄", "pending": "⏳", "blocked": "🚫"}.get(status, "⏳")
        lines.append(f"  {icon} {t.get('title', 'Untitled')} [{status}]")
        if status == "done":
            done += 1
    return f"Progress: {done}/{total}\n" + "\n".join(lines)


def _is_todo_task_completion(tool_name: str, response: Response) -> bool:
    """Detect if this was a todo:update that marked a task as done."""
    if not tool_name or "todo" not in tool_name.lower():
        return False
    msg = (response.message or "").lower()
    # Look for signals that a task was marked done
    return bool(
        re.search(r"status.*done|marked.*done|completed|status: done|→ done|✅", msg)
    )


def _all_tasks_done(agent) -> bool:
    """Check if ALL todo tasks are now complete."""
    todo_data = agent.context.data.get("_warroom_todo")
    if not todo_data or not isinstance(todo_data, list):
        return True  # No todo list means nothing to validate
    return all(t.get("status") in ("done", "skipped") for t in todo_data)


class WarRoomTaskValidation(Extension):
    """Triggers a War Room validation after a todo task is marked done.

    The validation checks:
    - Was the task actually completed correctly (quality check)?
    - Should an alternative approach be tried?
    - Has the overall goal been achieved?
    - Does the War Room need to reconvene for re-planning?

    No time limit or round limit — let the War Room think as much as needed.
    """

    async def execute(
        self,
        response: Response | None = None,
        tool_name: str = "",
        **kwargs,
    ):
        if not self.agent or not response:
            return

        # Only fire on todo task completions
        if not _is_todo_task_completion(tool_name, response):
            return

        # Don't validate if there's no meaningful todo list
        todo_data = self.agent.context.data.get("_warroom_todo")
        if not todo_data or not isinstance(todo_data, list) or len(todo_data) < 1:
            return

        # Build the validation problem
        todo_summary = _get_todo_summary(self.agent)
        all_done = _all_tasks_done(self.agent)

        # Get the last few messages for context about what was just done
        recent_context = ""
        try:
            messages = self.agent.history.current.messages[-4:]
            snippets = []
            for m in messages:
                if hasattr(m, 'content') and isinstance(m.content, str):
                    snippets.append(m.content[:300])
            recent_context = "\n---\n".join(snippets)[-1200:]
        except Exception:
            pass

        if all_done:
            problem = (
                "ALL WAR ROOM TASKS ARE NOW COMPLETE. Final validation required.\n\n"
                f"TODO LIST STATE:\n{todo_summary}\n\n"
                f"RECENT WORK CONTEXT:\n{recent_context}\n\n"
                "VALIDATE:\n"
                "1. Were all tasks completed successfully with quality results?\n"
                "2. Has the user's original goal been fully achieved?\n"
                "3. Are there any gaps, risks, or follow-up actions needed?\n"
                "4. Is the work ready for final delivery to the user?\n"
                "If everything looks good, give EXPLICIT approval for the agent "
                "to deliver the final response. If not, specify what needs to be "
                "fixed or what additional tasks should be added."
            )
            mode = "analysis"  # Full depth for final validation
        else:
            problem = (
                "A todo task was just marked as done. Quick validation needed.\n\n"
                f"TODO LIST STATE:\n{todo_summary}\n\n"
                f"RECENT WORK CONTEXT:\n{recent_context}\n\n"
                "VALIDATE:\n"
                "1. Was this task actually completed correctly?\n"
                "2. If not, should we retry with an alternative approach?\n"
                "3. Should the remaining tasks be re-prioritized?\n"
                "4. Any blockers or risks for the next tasks?\n"
                "Keep it concise — the agent still has more tasks to work through."
            )
            mode = "fast"  # Quick check for mid-plan validations

        try:
            from tools.think import Think

            PrintStyle(font_color="#c39bd3", padding=True).print(
                f"🔍 War Room task validation {'(FINAL)' if all_done else '(mid-plan)'}..."
            )

            think_tool = Think(
                agent=self.agent,
                name="think",
                method=None,
                args={
                    "problem": problem,
                    "mode": mode,
                    "_skip_todo_populate": True,  # Don't overwrite the todo list
                },
                message=problem,
                loop_data=self.agent.loop_data,
            )
            war_result = await think_tool.execute(
                problem=problem,
                mode=mode,
            )

            if war_result and war_result.message:
                synthesis = war_result.message
                persistent = self.agent.loop_data.params_persistent

                if all_done:
                    # Store the final validation result — response gate will check this
                    persistent["warroom_final_validation"] = synthesis
                    # Check if War Room approved using robust signal detection.
                    # Require explicit approval phrases and reject if negative
                    # signals are present (e.g. "not ready", "issues found").
                    text_lower = synthesis.lower()
                    rejection_signals = (
                        "not approved", "not ready", "don't deliver", "do not deliver",
                        "needs fix", "needs work", "issue", "problem", "fail",
                        "incorrect", "missing", "incomplete", "retry", "redo",
                        "must address", "should address", "must fix", "not complete",
                    )
                    has_rejection = any(s in text_lower for s in rejection_signals)
                    approval_phrases = (
                        "approved for delivery", "approved for final",
                        "green light", "ready for delivery", "ready to deliver",
                        "looks good", "all clear", "give approval",
                        "approve delivery", "approved to deliver",
                        "cleared for delivery",
                    )
                    has_approval = any(s in text_lower for s in approval_phrases)
                    # Rejection signals override approval signals
                    approved = has_approval and not has_rejection
                    persistent["warroom_final_approved"] = approved

                    if approved:
                        PrintStyle(font_color="#2a9d8f", padding=True).print(
                            "✅ War Room APPROVED — agent may deliver final response."
                        )
                    else:
                        PrintStyle(font_color="#e63946", padding=True).print(
                            "⚠️ War Room found issues — agent should address before responding."
                        )
                        # Inject the validation feedback as a directive
                        self.agent.loop_data.extras_temporary["warroom_validation_feedback"] = (
                            f"\n\n---\n"
                            f"## 🔍 WAR ROOM VALIDATION — ISSUES FOUND\n"
                            f"{synthesis[:2000]}\n"
                            f"---\n"
                            f"**Address the issues above before delivering your final response.**\n"
                        )
                else:
                    # Mid-plan validation — inject as temporary context
                    self.agent.loop_data.extras_temporary["warroom_validation_feedback"] = (
                        f"\n\n---\n"
                        f"## 🔍 War Room Task Review\n"
                        f"{synthesis[:1500]}\n"
                        f"---\n"
                    )

        except Exception as exc:
            import traceback
            PrintStyle(font_color="yellow", padding=True).print(
                f"⚠️ War Room task validation failed (non-critical): {exc}"
            )

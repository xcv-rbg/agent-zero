# extensions/python/tool_execute_before/_20_warroom_response_gate.py
# ─────────────────────────────────────────────────────────────────────────────
#  War Room — Final Response Gate
#  Prevents the agent from sending a final response to the user unless:
#    1. All todo tasks are complete (or no todo list exists), AND
#    2. The War Room has given explicit approval (warroom_final_approved)
#
#  If the gate blocks the response, it converts the tool call into a warning
#  message that tells the agent to finish its tasks first.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from helpers.extension import Extension
from helpers.errors import RepairableException
from helpers.print_style import PrintStyle


def _get_incomplete_tasks(agent) -> list[dict]:
    """Return list of incomplete todo tasks."""
    todo_data = agent.context.data.get("_warroom_todo")
    if not todo_data or not isinstance(todo_data, list):
        return []
    return [t for t in todo_data if t.get("status") not in ("done", "skipped")]


class WarRoomResponseGate(Extension):
    """Blocks the response tool if War Room tasks are incomplete or unapproved.

    The gate checks:
    1. Are there incomplete todo tasks? → Block and remind agent.
    2. All tasks done but no War Room approval? → Block and request validation.
    3. All tasks done AND War Room approved? → Allow response through.
    4. No todo list at all? → Allow response through (no War Room plan active).
    """

    async def execute(self, tool_name: str = "", tool_args: dict = {}, **kwargs):
        if not self.agent:
            return

        # Only gate the response tool
        normalized = (tool_name or "").strip().lower()
        if normalized != "response":
            return

        # Check todo state
        todo_data = self.agent.context.data.get("_warroom_todo")
        if not todo_data or not isinstance(todo_data, list) or len(todo_data) == 0:
            return  # No plan active — allow response

        incomplete = _get_incomplete_tasks(self.agent)
        persistent = self.agent.loop_data.params_persistent

        if incomplete:
            # Track how many times the gate has blocked — escalate language
            block_count = persistent.get("_response_gate_block_count", 0) + 1
            persistent["_response_gate_block_count"] = block_count

            # BLOCK: There are still tasks to complete
            task_list = "\n".join(
                f"  - {t.get('title', 'Untitled')} [{t.get('status', 'pending')}]"
                for t in incomplete[:5]
            )
            remaining = len(incomplete)

            if block_count >= 3:
                escalation = (
                    "🚨 THIS IS YOUR {n}TH BLOCKED ATTEMPT. STOP trying to respond. "
                    "Focus ONLY on completing the remaining tasks. Use `todo:next` NOW."
                ).format(n=block_count)
            else:
                escalation = (
                    "Use `todo:next` to get the next task and continue working."
                )

            warning = (
                f"⛔ RESPONSE BLOCKED — {remaining} War Room task(s) still incomplete:\n"
                f"{task_list}\n"
                f"You MUST complete all tasks before responding to the user. "
                f"{escalation}"
            )
            wmsg = self.agent.hist_add_warning(warning)
            PrintStyle(font_color="#e63946", padding=True).print(
                f"[ResponseGate] Blocked — {remaining} tasks incomplete (attempt #{block_count})"
            )
            self.agent.context.log.log(
                type="warning",
                content=warning,
                id=wmsg.id,
            )

            # Inject a persistent directive so the NEXT prompt includes a strong
            # reminder — history warnings alone can scroll out of context window.
            self.agent.loop_data.extras_persistent["_response_gate_directive"] = (
                "\n\n---\n"
                "## ⛔ RESPONSE BLOCKED — DO NOT ATTEMPT TO RESPOND\n"
                f"You have been blocked {block_count} time(s) from responding.\n"
                f"{remaining} War Room task(s) are still incomplete.\n"
                "**STOP attempting to use the response tool.** Instead:\n"
                "1. Use `todo:next` to pick up the next task\n"
                "2. Execute that task fully\n"
                "3. Mark it done with `todo:update`\n"
                "4. Repeat until ALL tasks are complete\n"
                "---\n"
            )

            # Raise to prevent the response tool from executing
            raise ResponseGateBlocked(warning)

        # All tasks done — check for War Room approval
        approved = persistent.get("warroom_final_approved", False)
        if not approved:
            # All tasks done but War Room hasn't validated — BLOCK until approved
            warning = (
                "⛔ RESPONSE BLOCKED — War Room final validation required.\n"
                "All tasks are complete, but the War Room has not yet approved delivery.\n"
                "Wait for the War Room validation to complete. If it hasn't triggered, "
                "use `think` with mode `analysis` to run a final review, then try responding again."
            )
            wmsg = self.agent.hist_add_warning(warning)
            PrintStyle(font_color="#f4a261", padding=True).print(
                "[ResponseGate] Blocked — awaiting War Room final approval."
            )
            self.agent.context.log.log(
                type="warning",
                content=warning,
                id=wmsg.id,
            )
            raise ResponseGateBlocked(warning)

        # All clear — War Room approved
        # Clear the block counter and directive since response is now allowed
        persistent.pop("_response_gate_block_count", None)
        self.agent.loop_data.extras_persistent.pop("_response_gate_directive", None)
        PrintStyle(font_color="#2a9d8f", padding=True).print(
            "[ResponseGate] ✅ War Room approved — response allowed."
        )


class ResponseGateBlocked(RepairableException):
    """Raised when the response gate blocks an early response attempt.

    Extends RepairableException so the framework catches it and sends
    the warning message to the agent, allowing it to continue working.
    """
    pass

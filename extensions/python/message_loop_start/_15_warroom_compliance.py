from helpers.extension import Extension
from agent import LoopData
from helpers.print_style import PrintStyle

# Tools that are always considered "compliant" — they are bookkeeping / planning
# tools and should never be flagged as non-compliance
_ALWAYS_COMPLIANT_TOOLS = frozenset({
    "todo",
    "think",
    "memory_tool",
    "response",
})


class WarroomCompliance(Extension):
    """
    Checks whether the agent followed the War Room's recommended tool call
    from the previous iteration. If non-compliance is detected repeatedly,
    escalates to forcing mode.
    """

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        try:
            if not self.agent:
                return

            # ── Auto-mark next pending todo as in_progress ────────────────────
            self._auto_mark_in_progress()

            persistent = loop_data.params_persistent
            recommended = persistent.get("warroom_recommended_tool")

            # Nothing to check if no recommendation was made
            if not recommended:
                return

            check_at = persistent.get("warroom_compliance_check_at")

            # First time seeing this recommendation — schedule check for next iteration
            if check_at is None:
                persistent["warroom_compliance_check_at"] = loop_data.iteration + 1
                return

            # Not yet time to check
            if loop_data.iteration < check_at:
                return

            # Time to check compliance — find the last tool the agent actually used
            last_tool = self._find_last_tool_in_history()

            if last_tool is None:
                # Agent didn't call any tool yet — defer check to next iteration
                persistent["warroom_compliance_check_at"] = loop_data.iteration + 1
                return

            # Clear the check-at marker so we don't re-check
            persistent.pop("warroom_compliance_check_at", None)

            # Normalize for comparison (strip whitespace, lowercase)
            recommended_norm = recommended.strip().lower()
            actual_norm = last_tool.strip().lower()

            # Always-compliant tools are never flagged
            if actual_norm in _ALWAYS_COMPLIANT_TOOLS:
                self._on_compliant(loop_data)
                return

            if actual_norm == recommended_norm:
                self._on_compliant(loop_data)
            else:
                self._on_noncompliant(loop_data, recommended, last_tool)

        except Exception as e:
            PrintStyle(font_color="yellow", padding=True).print(
                f"[WarroomCompliance] Error during compliance check: {e}"
            )

    def _find_last_tool_in_history(self) -> str | None:
        """Walk the agent's current topic messages backwards to find the last tool_name."""
        try:
            messages = self.agent.history.current.messages
            for msg in reversed(messages):
                content = msg.content
                if isinstance(content, dict):
                    tool_name = content.get("tool_name")
                    if tool_name:
                        return str(tool_name)
        except Exception:
            pass
        return None

    def _on_compliant(self, loop_data: LoopData):
        persistent = loop_data.params_persistent

        PrintStyle(font_color="#b3ffd9", padding=True).print(
            "[WarroomCompliance] Agent followed the recommended tool."
        )

        # Reset streak and clear recommendation
        persistent["warroom_noncompliance_streak"] = 0
        persistent.pop("warroom_recommended_tool", None)

        # If forcing was active, clear forcing state
        if persistent.get("warroom_forcing"):
            persistent.pop("warroom_forcing", None)
            # Clear the forced analysis extra from persistent extras
            loop_data.extras_persistent.pop("warroom_post_tool_analysis", None)
            PrintStyle(font_color="#b3ffd9", padding=True).print(
                "[WarroomCompliance] Forcing mode deactivated — compliance restored."
            )

    def _on_noncompliant(self, loop_data: LoopData, recommended: str, actual: str):
        persistent = loop_data.params_persistent

        streak = persistent.get("warroom_noncompliance_streak", 0) + 1
        persistent["warroom_noncompliance_streak"] = streak

        PrintStyle(font_color="orange", padding=True).print(
            f"[WarroomCompliance] Non-compliance #{streak}: "
            f"recommended '{recommended}', agent used '{actual}'."
        )

        if streak >= 2:
            persistent["warroom_forcing"] = True
            PrintStyle(font_color="red", padding=True).print(
                "[WarroomCompliance] Forcing mode ACTIVATED — streak threshold reached."
            )

    def _auto_mark_in_progress(self):
        """Auto-mark the next pending todo task as in_progress.

        This ensures the UI reflects what the agent is currently working on
        without requiring the agent to explicitly call todo:update.
        """
        try:
            todo_data = self.agent.context.data.get("_warroom_todo")
            if not todo_data or not isinstance(todo_data, list):
                return

            # Check if there's already an in_progress task
            has_active = any(t.get("status") == "in_progress" for t in todo_data)
            if has_active:
                return  # Already working on something

            # Find the first pending task and mark it in_progress
            for task in todo_data:
                if task.get("status") == "pending":
                    task["status"] = "in_progress"
                    PrintStyle(font_color="#a8d5e2", padding=False).print(
                        f"[WarroomCompliance] Auto-marked task '{task.get('title', '?')}' as in_progress"
                    )
                    break
        except Exception:
            pass  # Non-critical — never crash the loop

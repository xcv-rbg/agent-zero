# extensions/python/_functions/agent/Agent/handle_exception/end/_45_repair_tool_format_error.py
# ─────────────────────────────────────────────────────────────────────────────
#  Converts tool format validation errors (ValueError from validate_tool_request)
#  into RepairableException so the agent retries with correct JSON formatting
#  instead of crashing.
#
#  This handles the common case where the LLM outputs {"thoughts": [...]} without
#  a "tool_name" field — a formatting mistake that is self-repairable.
# ─────────────────────────────────────────────────────────────────────────────

from helpers.extension import Extension
from helpers.errors import RepairableException
from helpers.print_style import PrintStyle


# Error messages that come from agent.py validate_tool_request
_TOOL_FORMAT_ERRORS = (
    "Tool request must have a tool_name",
    "Tool request must have a tool_args",
    "Tool request must be a dictionary",
)


class RepairToolFormatError(Extension):
    async def execute(self, data: dict = {}, **kwargs):
        if not self.agent:
            return

        exception = data.get("exception")
        if not exception:
            return

        # Only convert ValueErrors that are tool format validation failures
        if not isinstance(exception, ValueError):
            return

        msg = str(exception)
        if not any(pattern in msg for pattern in _TOOL_FORMAT_ERRORS):
            return

        # Convert to RepairableException so the agent can self-repair
        PrintStyle(font_color="#f4a261", padding=True).print(
            f"[ToolFormatRepair] LLM output malformed tool request — converting to repairable error"
        )
        data["exception"] = RepairableException(
            f"{msg}\n\n"
            "Your response was missing a valid tool call. You MUST respond with a JSON "
            "object containing \"tool_name\" (string) and \"tool_args\" (dict) fields.\n"
            "Check your todo list with todo:list to see your current task, then "
            "re-issue the correct tool call."
        )

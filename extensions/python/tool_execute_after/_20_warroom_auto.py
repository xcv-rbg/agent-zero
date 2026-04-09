# extensions/python/tool_execute_after/_20_warroom_auto.py
# ─────────────────────────────────────────────────────────────────────────────
#  War Room — Automatic Post-Tool Analysis Extension
#  Fires after every significant tool execution.
#  Runs a mini War Room on the tool result and stores analysis for next prompt.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from helpers.extension import Extension
from helpers.tool import Response

# Tools that should never trigger a post-tool war room (would cause recursion
# or are too trivial to warrant multi-agent analysis)
_SKIP_TOOLS = frozenset({
    "think",         # recursion guard
    "response",      # final answer — no further analysis
    "memory_tool",   # memory ops are bookkeeping, not analysis targets
})

# Error-indicating patterns that ESCALATE complexity (force analysis mode)
_ERROR_PATTERNS = re.compile(
    r"\b(error|exception|traceback|failed|failure|denied|not found|"
    r"permission|eacces|enoent|segfault|crash|timeout|refused|"
    r"command not found|no module named|cannot find)\b",
    re.IGNORECASE,
)

# Large output patterns (binary dumps, long hex, base64 blobs) — skip these
_BINARY_PATTERN = re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")


class WarRoomAutoAnalysis(Extension):
    """Automatically runs a War Room analysis after significant tool executions."""

    async def execute(
        self,
        response: Response | None = None,
        tool_name: str = "",
        **kwargs,
    ):
        if not self.agent or not response:
            return

        # ── Skip list ─────────────────────────────────────────────────────────
        if tool_name in _SKIP_TOOLS:
            return

        result_text = response.message or ""
        result_len  = len(result_text)

        # Skip trivially short or empty results
        if result_len < 80:
            return

        # Skip binary/base64 blobs (e.g., raw binary tool output)
        if _BINARY_PATTERN.search(result_text[:200]):
            return

        # ── Decide mode ───────────────────────────────────────────────────────
        has_errors = bool(_ERROR_PATTERNS.search(result_text[:1000]))

        if has_errors:
            # Error analysis always gets targeted attention
            think_mode = "analysis"
        elif result_len > 500:
            # Long result — let the router decide
            think_mode = ""   # empty = router decides
        else:
            # Short-medium non-error result — lightweight execution mode
            think_mode = "execution"

        # ── Build the problem statement ───────────────────────────────────────
        truncated     = result_text[:800] if result_len > 800 else result_text
        error_context = result_text[:600] if has_errors else ""

        problem = (
            f"Tool '{tool_name}' just executed. Analyze the result and determine "
            f"the best next action.\n\nTOOL RESULT:\n{truncated}"
        )

        # ── Run War Room internally ───────────────────────────────────────────
        try:
            from tools.think import Think

            # Construct the Think tool directly (same signature as Tool.__init__):
            # agent, name, method, args, message, loop_data
            think_tool = Think(
                agent=self.agent,
                name="think",
                method=None,
                args={
                    "problem":       problem,
                    "error_context": error_context,
                    "mode":          think_mode,
                },
                message=problem,
                loop_data=self.agent.loop_data,
            )
            war_result = await think_tool.execute(
                problem=problem,
                error_context=error_context,
                mode=think_mode,
            )

            # Store synthesis for injection into the NEXT iteration's prompt.
            # params_temporary is cleared at the start of each inner loop
            # iteration, so we use params_persistent which survives until the
            # next message_loop_prompts_before picks it up and pops it.
            if war_result and war_result.message:
                self.agent.loop_data.params_persistent["warroom_post_tool"] = (
                    war_result.message
                )

        except Exception as exc:
            # Never crash the main agent loop over a War Room failure
            import traceback
            self.agent.context.log.log(
                type="warning",
                heading="⚠️ War Room auto-analysis failed (non-critical)",
                content=(
                    f"Tool: {tool_name}\nError: {exc}\n"
                    f"{traceback.format_exc()[-400:]}"
                ),
            )

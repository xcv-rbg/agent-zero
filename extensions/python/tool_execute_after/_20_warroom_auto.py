# extensions/python/tool_execute_after/_20_warroom_auto.py
# ─────────────────────────────────────────────────────────────────────────────
#  War Room — Automatic Post-Tool Analysis Extension
#  Fires after significant tool executions that contain ERRORS ONLY.
#  Non-error results are left for the main agent to handle directly.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from helpers.extension import Extension
from helpers.tool import Response

# Tools that should never trigger a post-tool war room (would cause recursion
# or are too trivial to warrant multi-agent analysis)
_SKIP_TOOLS = frozenset({
    "think",              # recursion guard
    "response",           # final answer — no further analysis
    "memory_tool",        # memory ops are bookkeeping, not analysis targets
    "browseros.list_pages",      # trivial listing
    "browseros.take_snapshot",   # observation only
    "browseros.take_screenshot", # observation only
    "search_engine",      # search results are self-explanatory
    "document_query",     # document query results are self-explanatory
    "skills_tool:load",
    "skills_tool:list"
})

# Error-indicating patterns that trigger auto-analysis
_ERROR_PATTERNS = re.compile(
    r"\b(error|exception|traceback|failed|failure|denied|not found|"
    r"permission|eacces|enoent|segfault|crash|timeout|refused|"
    r"command not found|no module named|cannot find)\b",
    re.IGNORECASE,
)

# Large output patterns (binary dumps, long hex, base64 blobs) — skip these
_BINARY_PATTERN = re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")


class WarRoomAutoAnalysis(Extension):
    """Runs a War Room analysis ONLY after tool executions that produce errors."""

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

        # # ── ONLY auto-analyze errors — let main agent handle success ──────────
        # has_errors = bool(_ERROR_PATTERNS.search(result_text[:1000]))
        # if not has_errors:
        #     return  # Non-error results don't need a War Room

        # ── Build the problem statement ───────────────────────────────────────
        truncated     = result_text[:2800] if result_len > 2800 else result_text
        error_context = result_text[:2600]

        problem = (
            f"Tool '{tool_name}' returned an error. Diagnose the root cause and "
            f"determine the best fix/next action.\n\nERROR OUTPUT:\n{truncated}"
        )

        # ── Run War Room internally (analysis mode for errors) ────────────────
        try:
            from tools.think import Think

            think_tool = Think(
                agent=self.agent,
                name="think",
                method=None,
                args={
                    "problem":       problem,
                    "error_context": error_context,
                    "mode":          "analysis",
                },
                message=problem,
                loop_data=self.agent.loop_data,
            )
            war_result = await think_tool.execute(
                problem=problem,
                error_context=error_context,
                mode="analysis",
            )

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

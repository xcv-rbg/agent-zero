# extensions/python/tool_execute_after/_20_warroom_auto.py
# ─────────────────────────────────────────────────────────────────────────────
#  War Room — Automatic Post-Tool Analysis Extension
#  Fires after significant tool executions that contain ERRORS ONLY.
#  Suppressed when the agent is actively working through a todo list to avoid
#  interrupting task execution flow.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import re
import time
from helpers.extension import Extension
from helpers.tool import Response

# Tools that should never trigger a post-tool war room (would cause recursion
# or are too trivial to warrant multi-agent analysis)
_SKIP_TOOLS = frozenset({
    "think",              # recursion guard
    "todo",               # todo ops are bookkeeping
    "response",           # final answer — no further analysis
    "memory_tool",        # memory ops are bookkeeping, not analysis targets
    "memory_load",        # memory loading
    "browseros.list_pages",      # trivial listing
    "browseros.take_snapshot",   # observation only
    "browseros.take_screenshot", # observation only
    "search_engine",      # search results are self-explanatory
    "document_query",     # document query results are self-explanatory
    "skills_tool",
    "skills_tool.load",
    "skills_tool.list",
})

# Minimum seconds between auto-triggered War Room sessions
_COOLDOWN_SECONDS = 120

# Maximum auto-triggers per context before disabling
_MAX_AUTO_TRIGGERS = 3

# Simple errors the agent can fix on its own — no War Room needed
_TRIVIAL_ERROR_PATTERNS = re.compile(
    r"TypeError: .*NoneType|"
    r"IndexError: list index out of range|"
    r"KeyError: |"
    r"SyntaxError: |"
    r"IndentationError: |"
    r"NameError: name .* is not defined|"
    r"FileNotFoundError: |"
    r"ZeroDivisionError|"
    r"ValueError: invalid literal",
    re.IGNORECASE,
)


def _normalize_tool_name(tool_name: str) -> str:
    """Normalize tool names so variant separators resolve consistently."""
    return (tool_name or "").strip().lower().replace(":", ".")


def _is_skipped_tool(tool_name: str) -> bool:
    """Return True for tools that should never trigger War Room auto-analysis."""
    normalized = _normalize_tool_name(tool_name)
    if normalized in _SKIP_TOOLS:
        return True

    # Hard guard: never auto-run War Room for skills tool family.
    if normalized == "skills_tool" or normalized.startswith("skills_tool."):
        return True

    return False

# Error-indicating patterns that trigger auto-analysis
_ERROR_PATTERNS = re.compile(
    r"\b(error|exception|traceback|failed|failure|denied|not found|"
    r"permission|eacces|enoent|segfault|crash|timeout|refused|"
    r"command not found|no module named|cannot find)\b",
    re.IGNORECASE,
)

# Large output patterns (binary dumps, long hex, base64 blobs) — skip these
_BINARY_PATTERN = re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")

# Patterns to extract a recommended tool name from War Room synthesis text
_TOOL_RECOMMEND_PATTERN = re.compile(
    r"(?:recommend(?:ed)?|use|execute|run|call|try)\s+(?:the\s+)?(?:tool\s+)?[`'\"]?(\w[\w.:]+)[`'\"]?",
    re.IGNORECASE,
)


def _extract_recommended_tool(synthesis: str) -> str | None:
    """Extract the recommended tool name from a War Room synthesis.
    
    First tries to parse the FOR_AGENT_ZERO JSON block for tool_name.
    Falls back to regex extraction from prose.
    """
    # Try structured JSON extraction first (most reliable)
    try:
        # Look for FOR_AGENT_ZERO JSON block in the synthesis
        json_match = re.search(r'\{[^{}]*"tool_name"\s*:\s*"([^"]+)"', synthesis[:3000])
        if json_match:
            return json_match.group(1)
        # Try full JSON parse for compact results
        start = synthesis.find("{")
        end = synthesis.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(synthesis[start:end + 1])
            faz = parsed.get("for_agent_zero", parsed)
            if isinstance(faz, dict) and faz.get("tool_name"):
                return faz["tool_name"]
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    # Fallback: regex extraction from prose
    m = _TOOL_RECOMMEND_PATTERN.search(synthesis[:1500])
    return m.group(1) if m else None


def _has_active_todo(agent) -> bool:
    """Check if there are pending/in_progress todo tasks the agent should work on."""
    todo_data = agent.context.data.get("_warroom_todo")
    if not todo_data or not isinstance(todo_data, list):
        return False
    for t in todo_data:
        if t.get("status") in ("pending", "in_progress"):
            return True
    return False


def _is_trivial_error(result_text: str) -> bool:
    """Return True if the error is simple enough for the agent to fix without War Room."""
    return bool(_TRIVIAL_ERROR_PATTERNS.search(result_text[:2000]))


class WarRoomAutoAnalysis(Extension):
    """Runs a War Room analysis ONLY after tool executions that produce errors.
    
    Suppressed when:
    - Agent has active todo tasks (let it work through the plan)
    - Cooldown hasn't elapsed since last auto-trigger
    - Max auto-triggers exceeded for this context
    - Error is trivially fixable (TypeError, KeyError, etc.)
    """

    async def execute(
        self,
        response: Response | None = None,
        tool_name: str = "",
        **kwargs,
    ):
        if not self.agent or not response:
            return

        # ── Skip list ─────────────────────────────────────────────────────────
        if _is_skipped_tool(tool_name):
            return

        result_text = response.message or ""
        result_len  = len(result_text)

        # Skip trivially short or empty results
        if result_len < 80:
            return

        # Skip binary/base64 blobs (e.g., raw binary tool output)
        if _BINARY_PATTERN.search(result_text[:200]):
            return

        # ── ONLY auto-analyze errors — let main agent handle success ──────────
        has_errors = bool(_ERROR_PATTERNS.search(result_text[:1000]))
        if not has_errors:
            return  # Non-error results don't need a War Room

        # ── Suppress if agent has active todo tasks ───────────────────────────
        # Let the agent work through its plan; it can call think manually if stuck
        if _has_active_todo(self.agent):
            return

        # ── Suppress trivial errors the agent can fix on its own ──────────────
        if _is_trivial_error(result_text):
            return

        # ── Cooldown: don't spam War Room sessions ───────────────────────────
        persistent = self.agent.loop_data.params_persistent
        last_trigger = persistent.get("warroom_auto_last_trigger", 0)
        now = time.time()
        if now - last_trigger < _COOLDOWN_SECONDS:
            return  # Too soon since last auto-trigger

        # ── Max triggers per context ──────────────────────────────────────────
        trigger_count = persistent.get("warroom_auto_trigger_count", 0)
        if trigger_count >= _MAX_AUTO_TRIGGERS:
            return  # Already triggered too many times

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
                    "mode":          "fast",   # Use fast mode for auto-triggers
                },
                message=problem,
                loop_data=self.agent.loop_data,
            )
            war_result = await think_tool.execute(
                problem=problem,
                error_context=error_context,
                mode="fast",
            )

            # Update cooldown and count
            persistent["warroom_auto_last_trigger"] = time.time()
            persistent["warroom_auto_trigger_count"] = trigger_count + 1

            if war_result and war_result.message:
                synthesis = war_result.message
                persistent["warroom_post_tool"] = synthesis

                # Extract recommended tool from synthesis for compliance tracking
                recommended = _extract_recommended_tool(synthesis)
                if recommended:
                    persistent["warroom_recommended_tool"] = recommended

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

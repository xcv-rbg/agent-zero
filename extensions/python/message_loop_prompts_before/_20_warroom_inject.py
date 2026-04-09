# extensions/python/message_loop_prompts_before/_20_warroom_inject.py
# ─────────────────────────────────────────────────────────────────────────────
#  War Room — Prompt Injection Extension
#  Injects the last War Room post-tool analysis into the prompt before the
#  next LLM call so the main agent's next decision is informed by the panel.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from helpers.extension import Extension
from agent import LoopData


class WarRoomPromptInjector(Extension):
    """Prepends last War Room synthesis to the next LLM prompt (one-shot)."""

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent or not loop_data:
            return

        # Pop ensures the analysis is injected exactly once and then discarded.
        # Stored in params_persistent by _20_warroom_auto so it survives the
        # params_temporary reset that occurs at the start of each inner loop
        # iteration.
        analysis = loop_data.params_persistent.pop("warroom_post_tool", None)
        if not analysis:
            return

        # Inject as an extras_temporary entry so it appears once in the prompt
        # (extras_temporary is cleared after each prepare_prompt call).
        loop_data.extras_temporary["warroom_post_tool_analysis"] = (
            "\n\n---\n"
            "## WAR ROOM POST-TOOL ANALYSIS (expert panel just ran)\n"
            f"{str(analysis)[:2000]}\n"
            "---\n"
            "Consider the War Room consensus above in your next decision. "
            "If the panel recommends a specific tool call, execute it.\n"
        )

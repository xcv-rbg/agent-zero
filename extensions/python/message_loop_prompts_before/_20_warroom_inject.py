# extensions/python/message_loop_prompts_before/_20_warroom_inject.py
# ─────────────────────────────────────────────────────────────────────────────
#  War Room — Prompt Injection Extension
#  Injects the last War Room post-tool analysis into the prompt before the
#  next LLM call so the main agent's next decision is informed by the panel.
#
#  Modes:
#    Normal  — one-shot injection via extras_temporary with mandatory language.
#    Forcing — persistent injection via extras_persistent with escalated
#              language; stays until compliance is confirmed externally.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from helpers.extension import Extension
from agent import LoopData

_NORMAL_TEMPLATE = (
    "\n\n---\n"
    "## ⚠️ WAR ROOM DIRECTIVE (expert panel consensus)\n"
    "{analysis}\n"
    "---\n"
    "**MANDATORY**: You MUST follow the War Room directive above. "
    "EXECUTE the recommended action immediately as your very next step. "
    "Do NOT ignore, skip, or override this directive.\n"
)

_FORCING_TEMPLATE = (
    "\n\n---\n"
    "## 🚨 WAR ROOM ESCALATED DIRECTIVE — IMMEDIATE COMPLIANCE REQUIRED\n"
    "{analysis}\n"
    "---\n"
    "**CRITICAL OVERRIDE**: This is an ESCALATED War Room directive. "
    "You are REQUIRED to execute the recommended tool call as your IMMEDIATE "
    "next action. Non-compliance has been detected previously. "
    "ANY deviation from this directive will be flagged. "
    "EXECUTE NOW.\n"
)


class WarRoomPromptInjector(Extension):
    """Injects War Room synthesis into the next LLM prompt with mandatory language."""

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

        analysis_text = str(analysis)[:2000]
        forcing = bool(loop_data.params_persistent.get("warroom_forcing"))

        # Carry forward the recommended tool for compliance tracking
        recommended = loop_data.params_persistent.get("warroom_recommended_tool")
        if recommended:
            # Keep it in params_persistent — compliance extension will consume it
            pass

        if forcing:
            # Persistent injection — stays in prompt across iterations until
            # compliance is confirmed and the key is removed externally.
            loop_data.extras_persistent["warroom_post_tool_analysis"] = (
                _FORCING_TEMPLATE.format(analysis=analysis_text)
            )
        else:
            # One-shot injection — cleared after the next prepare_prompt call.
            loop_data.extras_temporary["warroom_post_tool_analysis"] = (
                _NORMAL_TEMPLATE.format(analysis=analysis_text)
            )

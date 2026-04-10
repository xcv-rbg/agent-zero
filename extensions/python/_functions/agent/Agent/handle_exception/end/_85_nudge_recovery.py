# extensions/python/_functions/agent/Agent/handle_exception/end/_90_nudge_recovery.py
# ─────────────────────────────────────────────────────────────────────────────
#  War Room — Nudge Recovery (Last Resort)
#  When the error retry plugin (_80) exhausts its retries and the exception
#  still isn't handled, this extension clears the exception and uses the
#  context.nudge() mechanism to continue the agent from where it crashed
#  instead of respawning the whole agent from scratch.
#
#  Limited to MAX_NUDGE attempts per monologue to prevent infinite loops.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import threading
import time

from helpers.extension import Extension
from helpers.errors import RepairableException, HandledException
from helpers.print_style import PrintStyle


DATA_NUDGE_COUNTER = "_warroom_nudge_recovery_count"
MAX_NUDGE_ATTEMPTS = 2


class NudgeRecovery(Extension):
    """Last-resort crash recovery: nudge the agent instead of full restart."""

    async def execute(self, data: dict = {}, **kwargs):
        if not self.agent:
            return

        exception = data.get("exception")
        if not exception:
            return  # Already handled by earlier extension

        # Don't interfere with RepairableException / HandledException
        if isinstance(exception, (RepairableException, HandledException)):
            return

        # Check nudge counter to avoid infinite loops
        counter = self.agent.get_data(DATA_NUDGE_COUNTER) or 0
        if counter >= MAX_NUDGE_ATTEMPTS:
            PrintStyle(font_color="red", padding=True).print(
                f"[NudgeRecovery] Max nudge attempts ({MAX_NUDGE_ATTEMPTS}) reached — letting exception propagate."
            )
            return  # Let the exception propagate — truly fatal

        # Increment counter
        self.agent.set_data(DATA_NUDGE_COUNTER, counter + 1)

        error_msg = str(exception)[:500]
        PrintStyle(font_color="#f4a261", padding=True).print(
            f"[NudgeRecovery] Fatal error intercepted (attempt {counter + 1}/{MAX_NUDGE_ATTEMPTS}). "
            f"Using nudge to continue...\n  Error: {error_msg[:200]}"
        )

        # Log the recovery
        self.agent.context.log.log(
            type="warning",
            heading="Crash recovered via nudge",
            content=f"Fatal error: {error_msg}\nUsing nudge to continue from crash point.",
        )

        # Clear the exception to prevent propagation
        data["exception"] = None

        # Schedule nudge on a background thread (can't call from inside the loop)
        context = self.agent.context

        def _do_nudge():
            time.sleep(1.5)
            try:
                context.nudge()
                PrintStyle(font_color="#2a9d8f", padding=True).print(
                    "[NudgeRecovery] Agent nudged — continuing from crash point."
                )
            except Exception as exc:
                PrintStyle(font_color="red", padding=True).print(
                    f"[NudgeRecovery] Nudge failed: {exc}"
                )

        threading.Thread(target=_do_nudge, daemon=True).start()

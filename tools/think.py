import asyncio
import time

from helpers.tool import Tool, Response
from helpers.print_style import PrintStyle
from langchain_core.messages import SystemMessage, HumanMessage

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_BUDGET_SEC = 90
MAX_BUDGET_SEC = 180

# ── Expert Definitions ────────────────────────────────────────────────────────
# Compact system prompts (~80 words) for token efficiency.
# Round-specific instructions are injected dynamically per-call.

_GENERAL_EXPERTS = {
    "STRATEGIST": {
        "role": "Strategic Analyst",
        "icon": "icon://psychology",
        "color": "#aed6f1",
        "system": (
            "You are STRATEGIST on an expert thinking panel. "
            "Focus on: core challenge identification, hidden constraints, "
            "dependencies, and the optimal high-level approach. "
            "Think 3 steps ahead. Be decisive — no vague language. "
            "State your single most important insight first."
        ),
    },
    "CHALLENGER": {
        "role": "Devil's Advocate",
        "icon": "icon://gavel",
        "color": "#f1948a",
        "system": (
            "You are CHALLENGER on an expert thinking panel. "
            "Find flaws in every proposal, identify blind spots, "
            "stress-test assumptions, uncover hidden risks. "
            "If everyone agrees, find what they are missing. "
            "Always pair criticism with a concrete fix or alternative."
        ),
    },
    "EXECUTOR": {
        "role": "Implementer",
        "icon": "icon://build",
        "color": "#a9dfbf",
        "system": (
            "You are EXECUTOR on an expert thinking panel. "
            "Focus on: exact commands, tool parameters, file paths, "
            "concrete step-by-step actions, error handling. "
            "No hand-waving — if you cannot specify the exact action, say so."
        ),
    },
    "SYNTH": {
        "role": "Consensus Builder",
        "icon": "icon://balance",
        "color": "#f9e79f",
        "system": (
            "You are SYNTHESIZER on an expert thinking panel. "
            "Identify agreements, resolve disagreements by weighing evidence, "
            "produce clear actionable output. "
            "Your output IS the group's final decision."
        ),
    },
}

_SECURITY_EXPERTS = {
    "RED": {
        "role": "Offensive / Attacker",
        "icon": "icon://bug_report",
        "color": "#f1948a",
        "system": (
            "You are RED, offensive security expert on a security panel. "
            "Think like an attacker. Identify every exploitable vulnerability, "
            "attack vector, and trust boundary violation. "
            "For each finding: name it, explain exploitation, rate severity."
        ),
    },
    "BLUE": {
        "role": "Defensive / Hardening",
        "icon": "icon://security",
        "color": "#aed6f1",
        "system": (
            "You are BLUE, defensive security expert on a security panel. "
            "For each vulnerability: propose the specific code fix, config change, "
            "or control. Identify detection gaps — missing logging or monitoring. "
            "Prioritize by impact-to-effort ratio."
        ),
    },
    "ARCHITECT": {
        "role": "Threat Modeler",
        "icon": "icon://account_tree",
        "color": "#a9dfbf",
        "system": (
            "You are ARCHITECT, threat modeling expert on a security panel. "
            "Identify systemic design flaws: privilege escalation, broken trust, "
            "dangerous data flows, insecure defaults. Think STRIDE. "
            "Propose architectural fixes, not patches."
        ),
    },
    "AUDITOR": {
        "role": "Code Reviewer / OWASP",
        "icon": "icon://fact_check",
        "color": "#f9e79f",
        "system": (
            "You are AUDITOR, code review and compliance expert on a security panel. "
            "Map findings to OWASP Top 10 and CVE classes. "
            "Check for: hardcoded secrets, insecure deps, unsafe deserialization, "
            "missing validation, weak crypto."
        ),
    },
}

# ── Presets ────────────────────────────────────────────────────────────────────
# Each preset defines which experts debate in parallel and who synthesizes.

PRESETS = {
    "general": {
        "debaters": ["STRATEGIST", "CHALLENGER", "EXECUTOR"],
        "synthesizer": "SYNTH",
        "experts": _GENERAL_EXPERTS,
        "default_rounds": 2,
    },
    "security": {
        "debaters": ["RED", "BLUE", "ARCHITECT"],
        "synthesizer": "AUDITOR",
        "experts": _SECURITY_EXPERTS,
        "default_rounds": 2,
    },
}

# ── Round Instructions ────────────────────────────────────────────────────────
# Injected per-round to enforce conciseness and cross-pollination.

_PITCH_INSTRUCTION = (
    "Give your initial analysis in 150-200 words MAX. "
    "Be specific and actionable — name concrete tools, techniques, or steps. "
    "End with: KEY RISK: <one sentence>."
)

_REACT_INSTRUCTION = (
    "You have read everyone's positions on the blackboard. In 100-150 words:\n"
    "1. React to ONE specific point from another expert (name them)\n"
    "2. Refine OR defend your position with new reasoning\n"
    "3. Flag any risk the group is still missing\n"
    "End with: UPDATED POSITION: <one sentence>."
)

_SYNTHESIS_INSTRUCTION = (
    "Read the ENTIRE blackboard. Produce the group's final consensus.\n"
    "Resolve all disagreements by weighing the evidence presented.\n\n"
    "You MUST use EXACTLY this format:\n\n"
    "CONSENSUS PLAN:\n"
    "1. [concrete action step]\n"
    "2. [concrete action step]\n"
    "(as many as needed)\n\n"
    "KEY RISKS:\n"
    "- [risk + mitigation]\n\n"
    "DISSENT NOTES:\n"
    "- [any unresolved minority opinions worth preserving]\n\n"
    "FINAL RECOMMENDATION: [one sentence — the single most important first action]"
)


# ── Tool Class ────────────────────────────────────────────────────────────────

class Think(Tool):
    """
    War Room Multi-Agent Thinking Tool.

    Architecture: Blackboard + Parallel Micro-Rounds + Synthesis.
    Each debate round fires all experts simultaneously via asyncio.gather().
    Experts read the shared blackboard and cross-pollinate between rounds.
    A final synthesizer reads the full debate and produces the CONSENSUS PLAN.

    Tool args:
        problem (str):  Full problem statement with all relevant context.
        preset  (str):  "general" or "security". Default "general".
        rounds  (str):  Number of debate rounds (1-4). Default from preset (2).
        budget  (str):  Time budget in seconds. Default 90, max 180.
    """

    async def execute(self, **kwargs) -> Response:
        # ── Resolve arguments ────────────────────────────────────────────
        problem = (
            kwargs.get("problem")
            or self.args.get("problem", "")
            or self.message
        )
        if not problem or not problem.strip():
            return Response(
                message="Error: 'problem' argument is required.",
                break_loop=False,
            )

        preset_name = (
            kwargs.get("preset") or self.args.get("preset", "general")
        ).strip().lower()
        if preset_name not in PRESETS:
            preset_name = "general"
        preset = PRESETS[preset_name]

        try:
            req_rounds = int(
                kwargs.get("rounds") or self.args.get("rounds", "0")
            )
        except (ValueError, TypeError):
            req_rounds = 0
        total_rounds = (
            req_rounds if 1 <= req_rounds <= 4 else preset["default_rounds"]
        )

        try:
            budget_sec = max(
                30,
                min(
                    int(
                        kwargs.get("budget")
                        or self.args.get("budget", str(DEFAULT_BUDGET_SEC))
                    ),
                    MAX_BUDGET_SEC,
                ),
            )
        except (ValueError, TypeError):
            budget_sec = DEFAULT_BUDGET_SEC

        # ── Setup ────────────────────────────────────────────────────────
        all_experts = preset["experts"]
        debater_names = preset["debaters"]
        synth_name = preset["synthesizer"]

        h = PrintStyle(bold=True, font_color="#c39bd3", padding=True)
        c = PrintStyle(font_color="#d2b4de", padding=False)
        d = PrintStyle(font_color="#7f8c8d", padding=False)

        roster = ", ".join(debater_names) + f" + {synth_name}"
        h.print(
            f"War Room | {total_rounds} round(s) + synthesis | "
            f"preset: {preset_name} | budget: {budget_sec}s\n"
            f"Experts: {roster}\n"
            f"Problem: {problem[:160]}{'...' if len(problem) > 160 else ''}"
        )

        blackboard: list[dict] = []
        t0 = time.monotonic()
        rounds_completed = 0

        def elapsed() -> float:
            return time.monotonic() - t0

        def remaining() -> float:
            return max(0.0, budget_sec - elapsed())

        # ── Debate Rounds (parallel per round) ───────────────────────────
        for rnd in range(1, total_rounds + 1):
            if remaining() < 12:
                h.print(
                    f"Budget low ({elapsed():.0f}s) — jumping to synthesis."
                )
                break

            d.print(
                f"\n{'='*55}\n"
                f"  ROUND {rnd}/{total_rounds}  "
                f"({len(debater_names)} experts in parallel)\n"
                f"{'='*55}"
            )
            await self.set_progress(
                f"Round {rnd}/{total_rounds} — "
                f"{len(debater_names)} experts debating in parallel..."
            )

            bb_text = _format_blackboard(blackboard)
            first_round = rnd == 1
            instruction = _PITCH_INSTRUCTION if first_round else _REACT_INSTRUCTION

            # ── Parallel expert calls (streamed to WebUI) ────────────────
            async def _call(name: str, _rnd: int = rnd) -> dict:
                exp = all_experts[name]

                # Create log entry BEFORE the call so streaming has a target
                expert_log = self.agent.context.log.log(
                    type="tool",
                    heading=(
                        f"{exp['icon']} War Room — "
                        f"{name} ({exp['role']}) "
                        f"| Round {_rnd}"
                    ),
                    content="",
                    kvps={
                        "expert": name,
                        "role": exp["role"],
                        "round": str(_rnd),
                    },
                )

                if first_round:
                    human = (
                        f"TASK:\n{problem}\n\n"
                        f"INSTRUCTION: {instruction}"
                    )
                else:
                    human = (
                        f"TASK:\n{problem}\n\n"
                        f"=== BLACKBOARD ===\n{bb_text}\n\n"
                        f"INSTRUCTION: {instruction}"
                    )
                msgs = [
                    SystemMessage(content=exp["system"]),
                    HumanMessage(content=human),
                ]

                # Stream callback: pushes tokens to WebUI in real time
                async def _stream(chunk: str, _full: str) -> str | None:
                    if chunk:
                        expert_log.stream(content=chunk)
                    return None

                try:
                    text, _ = await self.agent.call_chat_model(
                        messages=msgs,
                        background=True,
                        response_callback=_stream,
                    )
                    text = text.strip()
                except Exception as exc:
                    text = (
                        f"[ERROR: {name} — "
                        f"{type(exc).__name__}: {exc}]"
                    )
                    expert_log.update(content=text)

                # Update log with final kvps (elapsed time)
                expert_log.update(kvps={"elapsed": f"{elapsed():.1f}s"})

                h.print(
                    f"[{name}]  {exp['role']}  | Round {_rnd}"
                )
                c.print(text)
                d.print("-" * 50)

                return {
                    "round": _rnd,
                    "expert": name,
                    "role": exp["role"],
                    "content": text,
                }

            raw_results = await asyncio.gather(
                *(_call(n) for n in debater_names),
                return_exceptions=True,
            )

            # Record to blackboard (skip exceptions — already logged in _call)
            for entry in raw_results:
                if isinstance(entry, dict):
                    blackboard.append(entry)
                elif isinstance(entry, BaseException):
                    h.print(f"[ERROR] Expert failed: {entry}")

            rounds_completed = rnd
            await self.agent.handle_intervention("")
            h.print(f"Round {rnd} done ({elapsed():.1f}s elapsed)")

        # ── Synthesis ────────────────────────────────────────────────────
        await self.set_progress(
            f"Synthesis — {synth_name} building consensus..."
        )
        d.print(f"\n{'='*55}\n  SYNTHESIS\n{'='*55}")

        synth_exp = all_experts[synth_name]
        bb_text = _format_blackboard(blackboard)
        synth_msgs = [
            SystemMessage(content=synth_exp["system"]),
            HumanMessage(
                content=(
                    f"TASK:\n{problem}\n\n"
                    f"=== FULL BLACKBOARD ===\n{bb_text}\n\n"
                    f"INSTRUCTION: {_SYNTHESIS_INSTRUCTION}"
                )
            ),
        ]

        # Create synthesis log entry for streaming
        synth_log = self.agent.context.log.log(
            type="tool",
            heading=(
                f"{synth_exp['icon']} War Room — "
                f"{synth_name} ({synth_exp['role']}) | Synthesis"
            ),
            content="",
            kvps={
                "expert": synth_name,
                "role": synth_exp["role"],
                "phase": "synthesis",
            },
        )

        async def _synth_stream(chunk: str, _full: str) -> str | None:
            if chunk:
                synth_log.stream(content=chunk)
            return None

        try:
            synthesis, _ = await self.agent.call_chat_model(
                messages=synth_msgs,
                background=True,
                response_callback=_synth_stream,
            )
            synthesis = synthesis.strip()
        except Exception as exc:
            synthesis = (
                f"[SYNTHESIS ERROR: {type(exc).__name__}: {exc}]\n"
                "Fallback: review the blackboard above and execute "
                "the most agreed-upon approach."
            )
            synth_log.update(content=synthesis)

        synth_log.update(kvps={"total_time": f"{elapsed():.1f}s"})
        h.print(f"[{synth_name}]  {synth_exp['role']}  | Synthesis")
        c.print(synthesis)

        # ── Assemble response ────────────────────────────────────────────
        # CRITICAL: Only the consensus plan goes into Response.message
        # (which feeds into the LLM's context via hist_add_tool_result).
        # The full debate transcript is already visible in WebUI logs
        # via the per-expert log entries created above.
        result = (
            f"War Room ({preset_name} | {rounds_completed} rounds | "
            f"{roster} | {elapsed():.1f}s)\n\n"
            f"{synthesis}\n\n"
            f"---\n"
            f"INSTRUCTION: Execute the CONSENSUS PLAN above step-by-step. "
            f"Use your tools to carry out each numbered action. "
            f"If you discover new information during execution that "
            f"contradicts the plan, adapt accordingly but follow the "
            f"overall strategy."
        )

        h.print(f"War Room complete ({elapsed():.1f}s) — consensus ready.")

        # Update the main tool log entry with completion summary
        self.log.update(
            heading=(
                f"icon://psychology "
                f"{self.agent.agent_name}: War Room Complete "
                f"({preset_name} | {rounds_completed}R | {elapsed():.1f}s)"
            ),
        )

        return Response(message=result, break_loop=False)

    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool",
            heading=(
                f"icon://psychology "
                f"{self.agent.agent_name}: War Room Thinking"
            ),
            content="",
            kvps=self.args,
            _tool_name=self.name,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_blackboard(board: list[dict]) -> str:
    """Format the blackboard entries as readable text for prompt injection."""
    if not board:
        return "(empty)"
    parts: list[str] = []
    cur_round = 0
    for e in board:
        if e["round"] != cur_round:
            cur_round = e["round"]
            parts.append(f"\n--- Round {cur_round} ---\n")
        parts.append(
            f"[{e['expert']} — {e['role']}]\n{e['content']}\n"
        )
    return "\n".join(parts)

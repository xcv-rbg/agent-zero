import asyncio
import time
import traceback
from datetime import datetime

from helpers.tool import Tool, Response
from helpers.print_style import PrintStyle
from langchain_core.messages import SystemMessage, HumanMessage

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_BUDGET_SEC = 60   # target: whole war room under 60s
MAX_BUDGET_SEC     = 90   # hard cap
_SYNTHESIS_RESERVE = 20   # always keep 20s for synthesis

_MAX_EXPERT_WORDS  = 70   # ~90 output tokens per expert per round
_MAX_SYNTH_WORDS   = 150  # ~200 output tokens for synthesis

# Chat bubble meta — WhatsApp group aesthetic
_CHAT_META = {
    "STRATEGIST": {"emoji": "🧠", "color": "#aed6f1", "short": "Strat"},
    "CHALLENGER":  {"emoji": "⚔️",  "color": "#f1948a", "short": "Chad"},
    "EXECUTOR":    {"emoji": "🔧", "color": "#a9dfbf", "short": "Exec"},
    "SYNTH":       {"emoji": "⚖️",  "color": "#f9e79f", "short": "Synth"},
    "RED":         {"emoji": "🔴", "color": "#f1948a", "short": "Red"},
    "BLUE":        {"emoji": "🔵", "color": "#aed6f1", "short": "Blue"},
    "ARCHITECT":   {"emoji": "🏗️", "color": "#a9dfbf", "short": "Arch"},
    "AUDITOR":     {"emoji": "📋", "color": "#f9e79f", "short": "Audit"},
}

# ══════════════════════════════════════════════════════════════════════════════
# EXPERT DEFINITIONS  (ultra-compact system prompts, ≤40 words each)
# ══════════════════════════════════════════════════════════════════════════════

_GENERAL_EXPERTS = {
    "STRATEGIST": {
        "role": "Strategy",
        "icon": "icon://psychology",
        "system": (
            "You are STRATEGIST in a fast expert chat. "
            "Identify the core challenge, key constraints, optimal approach. "
            "Be decisive, ultra-concise. No filler. Lead with your sharpest insight."
        ),
    },
    "CHALLENGER": {
        "role": "Devil's Advocate",
        "icon": "icon://gavel",
        "system": (
            "You are CHALLENGER in a fast expert chat. "
            "Find the biggest flaw or blind spot in current thinking. "
            "Always pair criticism with a concrete fix. Ultra-concise."
        ),
    },
    "EXECUTOR": {
        "role": "Implementation",
        "icon": "icon://build",
        "system": (
            "You are EXECUTOR in a fast expert chat. "
            "Give exact commands, file paths, tool flags, concrete steps. "
            "No hand-waving. If unsure, say so. Ultra-concise."
        ),
    },
    "SYNTH": {
        "role": "Consensus",
        "icon": "icon://balance",
        "system": (
            "You are SYNTH in a fast expert chat. "
            "Synthesize all positions into one clear decision. "
            "Resolve disagreements. Output is the group's final plan."
        ),
    },
}

_SECURITY_EXPERTS = {
    "RED": {
        "role": "Attacker",
        "icon": "icon://bug_report",
        "system": (
            "You are RED (offensive) in a fast security chat. "
            "Name exploitable vulns, attack vectors, trust violations. "
            "Each finding: name + severity + one-line exploit. Ultra-concise."
        ),
    },
    "BLUE": {
        "role": "Defender",
        "icon": "icon://security",
        "system": (
            "You are BLUE (defensive) in a fast security chat. "
            "For each vuln: exact fix or control. Flag missing logging. "
            "Prioritize by impact divided by effort. Ultra-concise."
        ),
    },
    "ARCHITECT": {
        "role": "Threat Model",
        "icon": "icon://account_tree",
        "system": (
            "You are ARCHITECT (threat model) in a fast security chat. "
            "Spot systemic design flaws: privilege escalation, broken trust, "
            "insecure defaults. Think STRIDE. Ultra-concise."
        ),
    },
    "AUDITOR": {
        "role": "Audit / Consensus",
        "icon": "icon://fact_check",
        "system": (
            "You are AUDITOR in a fast security chat. "
            "Map findings to OWASP/CVE. Synthesize team consensus into a "
            "prioritized fix plan. Ultra-concise."
        ),
    },
}

PRESETS = {
    "general": {
        "debaters":    ["STRATEGIST", "CHALLENGER", "EXECUTOR"],
        "synthesizer": "SYNTH",
        "experts":     _GENERAL_EXPERTS,
        "rounds":      2,
    },
    "security": {
        "debaters":    ["RED", "BLUE", "ARCHITECT"],
        "synthesizer": "AUDITOR",
        "experts":     _SECURITY_EXPERTS,
        "rounds":      2,
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# ROUND INSTRUCTIONS
# ══════════════════════════════════════════════════════════════════════════════

_PITCH = (
    f"Give your initial take in MAX {_MAX_EXPERT_WORDS} words. "
    "Be direct and specific — name exact tools, steps, or risks. "
    "End with: KEY RISK: <one sentence>."
)


def _react_prompt(others: str) -> str:
    return (
        f"The team posted:\n{others}\n\n"
        f"Reply in MAX {_MAX_EXPERT_WORDS} words:\n"
        "• Name ONE teammate and react to their specific point\n"
        "• Refine your position or flag a new gap\n"
        "End with: POSITION: <one sentence>."
    )


_SYNTH_PROMPT = (
    f"Read the team chat below. Produce the final plan in MAX {_MAX_SYNTH_WORDS} words.\n\n"
    "Use EXACTLY this format:\n"
    "PLAN:\n"
    "1. <action>\n"
    "2. <action>\n"
    "...\n\n"
    "RISKS: <bullet per risk + mitigation>\n\n"
    "CALL: <single most important first action, one sentence>"
)

# ══════════════════════════════════════════════════════════════════════════════
# CHAT FORMATTING HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _ts() -> str:
    return datetime.now().strftime("%I:%M %p")


def _bubble_heading(name: str, role: str, rnd=None) -> str:
    m = _CHAT_META.get(name, {"emoji": "💬", "short": name})
    tag = f" · Round {rnd}" if rnd else " · Synthesis"
    return f"{m['emoji']} {name} ({role}){tag}"


def _sep(label: str) -> str:
    return f"──────── {label} ────────"


def _format_others(blackboard: list, exclude: str, rnd: int) -> str:
    """Only what OTHER experts said in the given round — key token saver."""
    msgs = [
        f"{_CHAT_META.get(e['expert'], {}).get('emoji', '💬')} "
        f"{e['expert']}: {e['content']}"
        for e in blackboard
        if e["round"] == rnd and e["expert"] != exclude
    ]
    return "\n\n".join(msgs) if msgs else "(no messages yet)"


def _format_full_chat(blackboard: list) -> str:
    """Compact full transcript for synthesis — no duplication."""
    if not blackboard:
        return "(empty)"
    lines = []
    cur = 0
    for e in blackboard:
        if e["round"] != cur:
            cur = e["round"]
            lines.append(f"\n{_sep(f'Round {cur}')}")
        em = _CHAT_META.get(e["expert"], {}).get("emoji", "💬")
        lines.append(f"{em} {e['expert']}: {e['content']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# TOOL CLASS
# ══════════════════════════════════════════════════════════════════════════════

class Think(Tool):
    """
    War Room v3 — WhatsApp-Group Chat Style Multi-Expert Panel.

    Goals:
    - Entire run in <60s on any model
    - ~55% fewer tokens than v1:
        Round 1: experts receive only the problem (no blackboard overhead)
        Round 2+: experts receive ONLY teammates' new messages (not full problem)
        Word-capped replies (70 words expert / 150 words synthesis)
        Ultra-compact system prompts (~40 words vs 80)
        Synthesis gets compressed chat, not full problem re-injected
    - WhatsApp chat bubble aesthetic in WebUI (emoji + name + role + timestamp)
    - Zero repetition between rounds

    Args:
        problem : Full task with all context, code, errors.
        preset  : "general" | "security"  (default: general)
        rounds  : 1-3  (default: 2)
        budget  : seconds, default 60, max 90
    """

    async def execute(self, **kwargs) -> Response:

        # ── parse args ────────────────────────────────────────────────────────
        problem = (
            kwargs.get("problem") or self.args.get("problem", "") or self.message
        )
        if not problem or not problem.strip():
            return Response(message="Error: 'problem' is required.", break_loop=False)

        preset_name = (
            (kwargs.get("preset") or self.args.get("preset", "general"))
            .strip().lower()
        )
        if preset_name not in PRESETS:
            preset_name = "general"
        preset = PRESETS[preset_name]

        try:
            req_rounds = int(kwargs.get("rounds") or self.args.get("rounds", "0"))
        except (ValueError, TypeError):
            req_rounds = 0
        total_rounds = req_rounds if 1 <= req_rounds <= 3 else preset["rounds"]

        try:
            budget_sec = max(
                20,
                min(
                    int(kwargs.get("budget") or self.args.get("budget", str(DEFAULT_BUDGET_SEC))),
                    MAX_BUDGET_SEC,
                ),
            )
        except (ValueError, TypeError):
            budget_sec = DEFAULT_BUDGET_SEC

        # ── setup ─────────────────────────────────────────────────────────────
        all_experts = preset["experts"]
        debaters    = preset["debaters"]
        synth_name  = preset["synthesizer"]

        h = PrintStyle(bold=True, font_color="#c39bd3", padding=True)
        c = PrintStyle(font_color="#d2b4de", padding=False)
        d = PrintStyle(font_color="#7f8c8d", padding=False)

        roster = " · ".join(
            f"{_CHAT_META.get(n, {}).get('emoji', '')} {n}" for n in debaters
        ) + f" · {_CHAT_META.get(synth_name, {}).get('emoji', '')} {synth_name}"

        h.print(
            f"⚔️  War Room v3  |  {preset_name}  |  "
            f"{total_rounds} round(s)  |  budget {budget_sec}s\n"
            f"{roster}\n"
            f"🎯 {problem[:120]}{'…' if len(problem) > 120 else ''}"
        )

        blackboard: list = []
        t0 = time.monotonic()
        rounds_done = 0

        def elapsed() -> float:
            return time.monotonic() - t0

        def remaining() -> float:
            return max(0.0, budget_sec - elapsed())

        # ── debate rounds ─────────────────────────────────────────────────────
        for rnd in range(1, total_rounds + 1):

            if remaining() < _SYNTHESIS_RESERVE + 5:
                h.print(f"⏱ Budget tight ({elapsed():.0f}s elapsed) — jumping to synthesis.")
                break

            d.print(f"\n{_sep(f'Round {rnd} / {total_rounds}')}\n")
            await self.set_progress(
                f"Round {rnd}/{total_rounds} · {len(debaters)} experts typing…"
            )

            # FIX #1 — snapshot loop-locals as default args → closure safety
            is_first_snap = (rnd == 1)
            bb_snap       = list(blackboard)
            rnd_snap      = rnd

            async def _call(
                name: str,
                _rnd:   int  = rnd_snap,
                _first: bool = is_first_snap,
                _bb:    list = bb_snap,
            ) -> dict:
                exp  = all_experts[name]
                meta = _CHAT_META.get(name, {"emoji": "💬", "color": "#ccc"})

                expert_log = self.agent.context.log.log(
                    type="tool",
                    heading=_bubble_heading(name, exp["role"], _rnd),
                    content="",
                    kvps={
                        "expert": name,
                        "role":   exp["role"],
                        "round":  str(_rnd),
                        "time":   _ts(),
                    },
                )

                if _first:
                    # Round 1: only the problem — zero blackboard overhead
                    human = f"TASK:\n{problem}\n\n{_PITCH}"
                else:
                    # Round 2+: ONLY what teammates posted last round
                    # This is the biggest single token saver in v3
                    others = _format_others(_bb, name, _rnd - 1)
                    human  = _react_prompt(others)

                msgs = [
                    SystemMessage(content=exp["system"]),
                    HumanMessage(content=human),
                ]

                async def _stream(chunk: str, _full: str):
                    if chunk:
                        expert_log.stream(content=chunk)

                # FIX #4 — per-expert timeout with synthesis reserve
                call_budget = max(4.0, (remaining() - _SYNTHESIS_RESERVE) * 0.70)

                text: str
                try:
                    text, _ = await asyncio.wait_for(
                        self.agent.call_chat_model(
                            messages=msgs,
                            background=True,
                            response_callback=_stream,
                        ),
                        timeout=call_budget,
                    )
                    text = text.strip()
                    # FIX #2 — finalize log content on success path
                    expert_log.update(content=text)
                except asyncio.TimeoutError:
                    # FIX #3 — TimeoutError stamps expert log
                    text = f"⏱ [{name} timed out after {call_budget:.0f}s]"
                    expert_log.update(content=text)
                except Exception as exc:
                    # FIX #3 — any exception stamps expert log
                    tb = traceback.format_exc(limit=2)
                    text = f"❌ [{name} error: {type(exc).__name__}: {exc}]\n{tb}"
                    expert_log.update(content=text)

                expert_log.update(kvps={"elapsed": f"{elapsed():.1f}s"})
                c.print(f"{meta['emoji']} {name}  {_ts()}\n{text}\n")
                d.print("·" * 40)

                return {
                    "round":   _rnd,
                    "expert":  name,
                    "role":    exp["role"],
                    "content": text,
                }

            raw = await asyncio.gather(
                *(_call(n) for n in debaters),
                return_exceptions=True,
            )

            for entry in raw:
                if isinstance(entry, dict):
                    blackboard.append(entry)
                elif isinstance(entry, BaseException):
                    h.print(f"⚠️  gather error: {entry}")

            rounds_done = rnd

            # FIX #7 — handle_intervention wrapped
            try:
                await self.agent.handle_intervention("")
            except Exception as exc:
                h.print(f"⚠️  handle_intervention: {exc} — continuing")

            h.print(f"✅ Round {rnd} complete  ({elapsed():.1f}s)")

        # ── FIX #5 — empty blackboard guard ──────────────────────────────────
        if not blackboard:
            msg = (
                f"War Room v3 ({preset_name} | 0 rounds | {elapsed():.1f}s)\n\n"
                "⚠️  All expert calls failed — no consensus available.\n"
                "Proceed with your own best judgment."
            )
            self.log.update(
                heading=f"⚔️ War Room v3 — No Output ({preset_name})",
                content=msg,
            )
            return Response(message=msg, break_loop=False)

        # ── synthesis ─────────────────────────────────────────────────────────
        await self.set_progress(
            f"Synthesis · {_CHAT_META.get(synth_name, {}).get('emoji', '')} "
            f"{synth_name} building consensus…"
        )
        d.print(f"\n{_sep('Synthesis')}\n")

        synth_exp  = all_experts[synth_name]
        synth_meta = _CHAT_META.get(synth_name, {"emoji": "⚖️"})

        synth_log = self.agent.context.log.log(
            type="tool",
            heading=_bubble_heading(synth_name, synth_exp["role"]),
            content="",
            kvps={
                "expert": synth_name,
                "role":   synth_exp["role"],
                "phase":  "synthesis",
                "time":   _ts(),
            },
        )

        async def _synth_stream(chunk: str, _full: str):
            if chunk:
                synth_log.stream(content=chunk)

        # Synthesis: compressed chat + problem summary (no full re-inject)
        chat_text   = _format_full_chat(blackboard)
        synth_human = (
            f"Team chat:\n{chat_text}\n\n"
            f"Original task summary: {problem[:300]}{'…' if len(problem) > 300 else ''}\n\n"
            f"{_SYNTH_PROMPT}"
        )
        synth_msgs = [
            SystemMessage(content=synth_exp["system"]),
            HumanMessage(content=synth_human),
        ]

        synth_timeout = max(5.0, remaining())

        synthesis: str
        try:
            synthesis, _ = await asyncio.wait_for(
                self.agent.call_chat_model(
                    messages=synth_msgs,
                    background=True,
                    response_callback=_synth_stream,
                ),
                timeout=synth_timeout,
            )
            synthesis = synthesis.strip()
            synth_log.update(content=synthesis)
        except asyncio.TimeoutError:
            synthesis = (
                f"⏱ [Synthesis timed out after {synth_timeout:.0f}s]\n"
                "Review the team chat above and execute the most agreed-upon approach."
            )
            synth_log.update(content=synthesis)
        except Exception as exc:
            tb = traceback.format_exc(limit=2)
            synthesis = f"❌ [Synthesis error: {type(exc).__name__}: {exc}]\n{tb}"
            synth_log.update(content=synthesis)

        synth_log.update(kvps={"total_elapsed": f"{elapsed():.1f}s"})
        h.print(
            f"{synth_meta.get('emoji', '⚖️')} {synth_name}  {_ts()}\n"
            f"{synthesis}\n\n⚔️  War Room v3 complete  {elapsed():.1f}s ✅"
        )

        # ── final response ────────────────────────────────────────────────────
        result = (
            f"War Room v3 ({preset_name} | {rounds_done} round(s) | "
            f"{len(debaters) + 1} experts | {elapsed():.1f}s)\n\n"
            f"{synthesis}\n\n"
            "---\n"
            "INSTRUCTION: Execute the PLAN above step by step with your tools. "
            "Adapt individual steps as you discover new information but keep the "
            "overall strategy. If a step fails or you hit a blocker, call think "
            "again with updated context."
        )

        # FIX #6 — main log entry now includes content=
        self.log.update(
            heading=(
                f"⚔️ {self.agent.agent_name}: War Room v3 Complete "
                f"({preset_name} | {rounds_done}R | {elapsed():.1f}s)"
            ),
            content=result,
        )

        return Response(message=result, break_loop=False)

    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool",
            heading=f"⚔️ {self.agent.agent_name}: War Room v3",
            content="",
            kvps=self.args,
            _tool_name=self.name,
        )

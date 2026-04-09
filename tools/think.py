# tools/think.py
# ─────────────────────────────────────────────────────────────────────────────
#  Agent Zero — War Room Multi-Agent Thinking Tool  v2.0
#  Merged: SWARM Expert Panel + Blackboard Architecture + Parallel Micro-Rounds
#
#  INSTALL: drop this file at tools/think.py in your Agent Zero repo.
#  No changes to agent.py are required.
#
#  Features:
#    • Complexity Router: classifies every task before spinning agents
#    • Parallel micro-rounds: all panelists fire simultaneously via asyncio.gather
#    • Shared blackboard: every agent reads all prior round outputs
#    • Divergence detection: Jaccard similarity on suggested_action fields
#    • Flash Debate: only dissenters fire if consensus not reached after round 2
#    • Synthesizer: ALWAYS called last, produces strict JSON tool request
#    • War Room Model: uses build_war_model() from the _model_config plugin — configure via WebUI Models settings
#    • Tool-request hardening: Synthesizer output = valid Agent Zero JSON
#    • Environment diagnostics: detects missing tools/permissions and adds
#      repair steps to the consensus plan
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Optional

from helpers.tool import Tool, Response
from helpers.print_style import PrintStyle
from langchain_core.messages import SystemMessage, HumanMessage

# ─────────────────────────────────────────────────────────────────────────────
#  COMPLEXITY ROUTER — 1 fast LLM call, structured JSON output
# ─────────────────────────────────────────────────────────────────────────────
_ROUTER_SYSTEM = """You are a task complexity classifier for an AI agent system.
Output ONLY a JSON object — no prose, no markdown fences.

Schema:
{
  "complexity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "TRIVIAL",
  "agent_count": 6 | 5 | 4 | 3 | 2 | 1,
  "rounds": 3 | 2 | 1,
  "mode": "planning" | "analysis" | "execution"
}

Rules:
CRITICAL / 6 agents / 3 rounds — initial brief for a major operation, novel multi-stage attack,
  architecture-level design, high-stakes security-critical decisions, ambiguous zero-day situations,
  cross-system threat modeling, situations where a wrong decision has irreversible consequences.
HIGH / 4-5 agents / 2-3 rounds — security work, complex planning, ambiguous goals,
  multi-step research, first encounter with a problem, risky or irreversible actions,
  error analysis where the cause is unclear.
MEDIUM / 3 agents / 2 rounds — result analysis, moderate complexity, debugging known errors,
  interpreting tool output where context is partially understood.
LOW / 2 agents / 1 round — simple follow-up in an established plan, quick analysis of
  a clear tool result, single-step action with limited unknowns.
TRIVIAL / 1 agent / 1 round — lookup, echo, file listing, simple grep, well-understood next step
  where the correct tool call is obvious.

agent_count is determined by complexity:
  CRITICAL → 6
  HIGH → 4 or 5 (5 if involves adversarial/red-team context)
  MEDIUM → 3
  LOW → 2
  TRIVIAL → 1

mode=planning   : deciding what to do, initial task setup
mode=analysis   : interpreting results / diagnosing errors / evaluating findings
mode=execution  : plan exists, need precise tool parameters
"""

# ─────────────────────────────────────────────────────────────────────────────
#  FIVE PANELIST PERSONAS — router picks subset based on agent_count
#  agent_count 1 → [EXECUTOR]
#  agent_count 3 → [STRATEGIST, EXECUTOR, CRITIC]
#  agent_count 4 → [STRATEGIST, CHALLENGER, EXECUTOR, RESEARCHER]
#  agent_count 5 → all five (used when escalation adds CRITIC mid-session)
# ─────────────────────────────────────────────────────────────────────────────
_ALL_PANELISTS: list[dict[str, str]] = [
    {
        "name": "STRATEGIST",
        "role": "Strategic Planner",
        "icon": "icon://psychology",
        "color": "#aed6f1",
        "system": (
            "You are STRATEGIST on a war room panel. "
            "Focus: goal decomposition, attack-surface mapping, risk/reward, prioritisation. "
            "Think 3 steps ahead. Be decisive. Name concrete first actions.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"1-sentence position","suggested_action":"specific next action",'
            '"key_risk":"top risk","confidence":0.8}'
        ),
    },
    {
        "name": "CHALLENGER",
        "role": "Devil's Advocate",
        "icon": "icon://gavel",
        "color": "#f1948a",
        "system": (
            "You are CHALLENGER on a war room panel. "
            "Focus: find flaws in every plan, stress-test assumptions, blind spots. "
            "Always propose a concrete fix for every flaw. Reference specific panelist claims.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"1-sentence challenge","suggested_action":"alternative action",'
            '"key_risk":"what group is missing","confidence":0.8}'
        ),
    },
    {
        "name": "EXECUTOR",
        "role": "Implementer",
        "icon": "icon://build",
        "color": "#a9dfbf",
        "system": (
            "You are EXECUTOR on a war room panel. "
            "Focus: exact commands, tool names, file paths, parameters. No hand-waving. "
            "If you cannot specify the exact action, say so.\n"
            "Available tools: code_execution, browser_open, browser_do, search_engine, "
            "document_query, skills_tool, call_subordinate, memory_tool.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"implementation approach","suggested_action":"exact tool or command",'
            '"key_risk":"top execution risk","confidence":0.8}'
        ),
    },
    {
        "name": "RESEARCHER",
        "role": "Knowledge Specialist",
        "icon": "icon://search",
        "color": "#d7bde2",
        "system": (
            "You are RESEARCHER on a war room panel. "
            "Focus: relevant CVEs, techniques, prior art, documentation, known patterns. "
            "Always cite specific technique names, CVE IDs, or tool names.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"key knowledge for this task","suggested_action":"research-backed recommendation",'
            '"key_risk":"known pitfall from prior art","confidence":0.8}'
        ),
    },
    {
        "name": "CRITIC",
        "role": "Quality Reviewer",
        "icon": "icon://fact_check",
        "color": "#f9e79f",
        "system": (
            "You are CRITIC on a war room panel. "
            "Focus: edge cases, silent failures, false positives, completeness gaps. "
            "If a plan can fail silently or miss something important, flag it.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"quality concern","suggested_action":"test or verification step",'
            '"key_risk":"what could fail silently","confidence":0.8}'
        ),
    },
    {
        "name": "TACTICIAN",
        "role": "Red Team Tactician",
        "icon": "icon://military_tech",
        "color": "#f8c471",
        "system": (
            "You are TACTICIAN on a war room panel. "
            "Focus: adversarial thinking, bypass strategies, chaining vulnerabilities, "
            "operational security, anti-detection, prioritizing highest-impact paths. "
            "Always think from an attacker's perspective: what's the fastest path to impact?\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"adversarial assessment","suggested_action":"highest-impact attack path",'
            '"key_risk":"detection or mitigation risk","confidence":0.8}'
        ),
    },
]

_PANELIST_SUBSET: dict[int, list[str]] = {
    1: ["EXECUTOR"],
    2: ["STRATEGIST", "EXECUTOR"],
    3: ["STRATEGIST", "EXECUTOR", "CRITIC"],
    4: ["STRATEGIST", "CHALLENGER", "EXECUTOR", "RESEARCHER"],
    5: ["STRATEGIST", "CHALLENGER", "EXECUTOR", "RESEARCHER", "CRITIC"],
    6: ["STRATEGIST", "CHALLENGER", "EXECUTOR", "RESEARCHER", "CRITIC", "TACTICIAN"],
}

# ─────────────────────────────────────────────────────────────────────────────
#  SYNTHESIZER — always called last, always produces the final decision
# ─────────────────────────────────────────────────────────────────────────────
_SYNTHESIZER: dict[str, str] = {
    "name": "SYNTHESIZER",
    "role": "Consensus Builder & Judge",
    "icon": "icon://balance",
    "color": "#f0e6c8",
    "system": (
        "You are SYNTHESIZER, the final judge on a war room panel.\n"
        "Your output IS the group decision. The main AI agent executes it directly.\n\n"
        "You MUST output ONLY this JSON object — no prose, no markdown fences:\n"
        "{\n"
        '  "consensus_action": "one sentence — what to do next",\n'
        '  "confidence": 0.85,\n'
        '  "key_risks": ["risk 1", "risk 2"],\n'
        '  "dissent_notes": ["any unresolved minority concern"],\n'
        '  "reasoning_trace": "brief: how consensus emerged from the debate",\n'
        '  "for_agent_zero": {\n'
        '    "thoughts": ["why this action was chosen based on the panel"],\n'
        '    "headline": "short display headline",\n'
        '    "tool_name": "code_execution",\n'
        '    "tool_args": {"runtime": "python", "code": "# exact code here"}\n'
        "  }\n"
        "}\n\n"
        "RULES FOR for_agent_zero:\n"
        "- tool_name MUST be exactly one of: code_execution, browser_open, browser_do,\n"
        "  search_engine, document_query, skills_tool, call_subordinate, memory_tool, response, think\n"
        "- tool_args MUST match the chosen tool's expected argument schema exactly.\n"
        "- For shell/terminal: {\"runtime\": \"terminal\", \"code\": \"bash command here\"}\n"
        "- For Node.js: {\"runtime\": \"nodejs\", \"code\": \"// js code\"}\n"
        "- For Python: {\"runtime\": \"python\", \"code\": \"# python code\"}\n"
        "- For browser: browser_open → {\"url\": \"...\"}; browser_do → {\"action\": \"...\"}\n"
        "- For search: {\"query\": \"search query\"}\n"
        "- For skills: {\"action\": \"load\", \"skill\": \"skill_name\"}\n"
        "- If multiple steps needed: tool_name=\"response\", tool_args={\"text\":\"Full numbered plan\"}\n"
        "- thoughts MUST explain WHY based on the debate — not just restate the action.\n"
        "- Output ONLY the JSON. No text before or after.\n\n"
        "ENVIRONMENT REPAIR RULES:\n"
        "If panelists identified a missing tool, permission error, or env issue:\n"
        "  - Include a repair step FIRST in the plan (install package, chmod, use fallback)\n"
        "  - Use code_execution with terminal runtime to install/fix before the main action\n"
        "  - Common repairs:\n"
        "    • 'command not found' → terminal: apt-get install / npm install / pip install\n"
        "    • 'Module not found: ws' → terminal: cd /tmp && npm install ws\n"
        "    • 'Permission denied' → terminal: chmod +x <file> OR sudo <cmd>\n"
        "    • /bin/sh incompatibility → runtime='terminal', prefix cmd with 'bash -lc'\n"
        "    • Python module missing → terminal: pip install <package>"
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
#  ENVIRONMENT DIAGNOSTICS PATTERNS
#  These are checked against tool stderr/stdout to auto-suggest repair actions
# ─────────────────────────────────────────────────────────────────────────────
_ENV_PATTERNS: list[tuple[str, str]] = [
    (r"command not found[:\s]+(\S+)",      "install missing command: {0}"),
    (r"Cannot find module '([^']+)'",       "npm install {0}"),
    (r"No module named '([^']+)'",          "pip install {0}"),
    (r"ModuleNotFoundError.*'([^']+)'",     "pip install {0}"),
    (r"Permission denied",                  "check permissions: chmod +x or run as root"),
    (r"EACCES",                             "Node permission denied: check file paths or run chmod"),
    (r"ENOENT.*'([^']+)'",                 "file not found: {0} — verify path exists"),
    (r"bash: .*: not found",               "install package or check PATH"),
    (r"/bin/sh.*syntax error",             "use runtime=terminal with bash -lc instead of sh"),
]


def _diagnose_error(stderr: str, stdout: str) -> list[str]:
    """
    Scan stderr/stdout for known error patterns.
    Returns list of human-readable repair suggestions.
    """
    text = (stderr or "") + "\n" + (stdout or "")
    suggestions: list[str] = []
    for pattern, template in _ENV_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                suggestion = template.format(*m.groups())
            except IndexError:
                suggestion = template
            suggestions.append(suggestion)
    return suggestions


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN TOOL CLASS
# ─────────────────────────────────────────────────────────────────────────────
class Think(Tool):
    """
    War Room Multi-Agent Thinking Tool for Agent Zero.

    Runs a smart panel of expert sub-LLM calls before the main agent
    executes any tool on a complex task.

    Args:
        problem (str): Full problem statement with all context, code, errors.
        error_context (str): Optional — stderr/stdout from a failed tool call.
                             Triggers environment diagnostics mode.
        mode (str): Optional override — "planning" | "analysis" | "execution".
                    If omitted, the Complexity Router decides automatically.
    """

    # ── public entry point ────────────────────────────────────────────────────
    async def execute(
        self,
        problem: str = "",
        error_context: str = "",
        mode: str = "",
        **kwargs: Any,
    ) -> Response:

        # ── resolve args ──────────────────────────────────────────────────────
        if not problem:
            problem = self.args.get("problem", "") or self.message
        error_context = error_context or self.args.get("error_context", "")
        mode_override = mode or self.args.get("mode", "")

        p_head = PrintStyle(bold=True,  font_color="#c39bd3", padding=True)
        p_body = PrintStyle(font_color="#d2b4de", padding=False)
        p_div  = PrintStyle(font_color="#7f8c8d",  padding=False)

        p_head.print(
            f"🏛️  War Room activated\n"
            f"Problem: {problem[:160]}{'...' if len(problem) > 160 else ''}"
        )

        # Runtime diagnostics for war model usage visibility.
        self._war_fallback_logged = False
        self._war_fallback_count = 0
        self._war_model_resolved = "unknown"

        # ── Cache war model once for the entire session ──────────────
        self._cached_war_llm = None
        try:
            from plugins._model_config.helpers.model_config import (
                build_war_model,
                get_war_model_display,
                get_config,
            )
            self._cached_war_llm = build_war_model(self.agent)
            self._war_model_resolved = get_war_model_display(self.agent)
        except Exception:
            pass  # Will fall back to main model in _llm_call

        start_time = time.time()

        # ── Step 0: Complexity Router ─────────────────────────────────────────
        await self.set_progress("⚡ Complexity Router classifying task...")
        route = await self._run_router(problem, mode_override)
        agent_count: int = route.get("agent_count", 4)
        max_rounds:  int = route.get("rounds", 2)
        complexity:  str = route.get("complexity", "HIGH")
        task_mode:   str = route.get("mode", "planning")

        # environment-error mode always uses analysis config
        if error_context:
            complexity  = "MEDIUM"
            agent_count = 3
            max_rounds  = 2
            task_mode   = "analysis"
            env_hints = _diagnose_error(error_context, "")
        else:
            env_hints = []

        p_body.print(
            f"📊 Complexity: {complexity} | "
            f"Agents: {agent_count}/6 | "
            f"Max rounds: {max_rounds} | "
            f"Mode: {task_mode}"
        )

        # Show resolved War Room model at the start for traceability.
        try:
            from plugins._model_config.helpers.model_config import get_war_model_display

            self._war_model_resolved = get_war_model_display(self.agent)
            p_body.print(f"🧠 War model resolved: {self._war_model_resolved}")
        except Exception:
            pass

        # select panelists
        names = _PANELIST_SUBSET.get(agent_count, _PANELIST_SUBSET[4])
        panelists = [p for p in _ALL_PANELISTS if p["name"] in names]

        # ── Single consolidated log entry for the entire War Room session ─────
        # Reuse the active tool log when available so live updates appear in the
        # same UI tab the user already has open.
        self._war_log = getattr(self, "log", None)
        if not self._war_log:
            # Extension or direct call — create a proper tool log so the WebUI
            # routes it through drawMessageWarRoom (requires type="tool" +
            # _tool_name="think" in kvps).
            import uuid
            self._war_log = self.agent.context.log.log(
                type="tool",
                heading=f"🏛️ {self.agent.agent_name} — War Room Deliberating…",
                content="Initializing War Room...",
                kvps=self.args or {},
                _tool_name="think",
                id=str(uuid.uuid4()),
            )
        else:
            self._war_log.update(
                heading=f"🏛️ {self.agent.agent_name} — War Room Deliberating…",
            )
        self._war_live_sections = [
            (
                f"📊 {complexity} | {len(panelists)} panelists | "
                f"{max_rounds} rounds | Model: {self._war_model_resolved}\n"
            )
        ]
        self._war_live_preview = ""
        self._refresh_war_log_content()

        # ── Blackboard ────────────────────────────────────────────────────────
        blackboard: list[dict] = []   # list of round dicts
        consensus_score: float = 0.0

        # ── Micro-Round Loop ──────────────────────────────────────────────────
        for round_num in range(1, max_rounds + 1):

            p_div.print(f"\n{'═'*55}\n  ROUND {round_num}/{max_rounds}\n{'═'*55}")

            self._append_war_section(
                f"\n━━ Round {round_num} ━━━━━━━━━━━━━━━━━\n",
            )

            round_entries = await self._run_parallel_round(
                panelists=panelists,
                problem=problem,
                blackboard=blackboard,
                round_num=round_num,
                env_hints=env_hints,
                p_body=p_body,
            )
            blackboard.append({"round": round_num, "entries": round_entries})

            # ── Divergence Detection ──────────────────────────────────────────
            consensus_score = self._compute_consensus(round_entries)
            p_body.print(
                f"📐 Consensus score after round {round_num}: "
                f"{consensus_score:.2f}"
            )
            self._append_war_section(
                f"\n📐 Consensus: {consensus_score:.2f}\n",
            )

            if consensus_score >= 0.70:
                p_body.print("✅ Consensus reached — skipping remaining rounds")
                self._append_war_section("\n✅ Consensus reached — skipping remaining rounds\n")
                break

            if round_num < max_rounds and consensus_score < 0.50:
                # escalate: add CRITIC if not already present
                if not any(p["name"] == "CRITIC" for p in panelists):
                    critic = next(p for p in _ALL_PANELISTS if p["name"] == "CRITIC")
                    panelists.append(critic)
                    p_body.print("⚠️  Low consensus — escalating: added CRITIC")

        # ── Flash Debate (dissenters only, if MEDIUM+ complexity needed) ──────
        if consensus_score < 0.60 and len(blackboard) >= 2 and agent_count >= 3:
            p_div.print("\n⚡ Flash Debate — dissenters vs majority\n")
            flash_entries = await self._run_flash_debate(
                panelists=panelists,
                problem=problem,
                blackboard=blackboard,
                consensus_score=consensus_score,
                p_body=p_body,
            )
            if flash_entries:
                blackboard.append({"round": "flash", "entries": flash_entries})

        # ── SYNTHESIZER — always called, no exceptions ─────────────────────────
        await self.set_progress("🔬 SYNTHESIZER building final consensus...")
        p_div.print(f"\n{'─'*55}\n  SYNTHESIZER (Final Judge)\n{'─'*55}")

        synthesis_raw = await self._run_synthesizer(
            problem=problem,
            blackboard=blackboard,
            consensus_score=consensus_score,
            env_hints=env_hints,
        )
        synthesis = self._safe_json(synthesis_raw)

        elapsed = time.time() - start_time

        # ── Format final response ─────────────────────────────────────────────
        round_count = len([b for b in blackboard if b["round"] != "flash"])

        # Verbose transcript → appended to the consolidated war log entry
        verbose_log = self._format_verbose_log(
            blackboard=blackboard,
            synthesis=synthesis,
            synthesis_raw=synthesis_raw,
            elapsed=elapsed,
            panelists=panelists,
            round_count=round_count,
        )
        self._war_log.update(
            heading=(
                f"🏛️ War Room — {elapsed:.0f}s | "
                f"Conf: {synthesis.get('confidence', '?')} | "
                f"{len(panelists)} panelists"
            ),
            update_progress="persistent",
            content=verbose_log,
        )

        # Compact result → agent message history (~200-400 tokens)
        compact = self._format_compact_result(
            synthesis=synthesis,
            synthesis_raw=synthesis_raw,
            elapsed=elapsed,
            panelists=panelists,
            round_count=round_count,
        )

        p_head.print(
            f"🏛️  War Room complete — {elapsed:.1f}s | "
            f"Confidence: {synthesis.get('confidence', '?')}"
        )

        return Response(message=compact, break_loop=False)

    # ─────────────────────────────────────────────────────────────────────────
    #  INTERNAL HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    async def _llm_call(
        self,
        messages: list,
        temperature: float = 0.2,
        panelist_name: str = "",
    ) -> str:
        """
        Call the War Room dedicated model (or fall back to main chat model).
        Uses the cached war model from execute() when available.
        Streams tokens to the WebUI in real-time via response_callback.
        Falls back to the agent's main chat model on any exception.
        """
        war_display = getattr(self, "_war_model_resolved", "unknown")
        war_explicit = False

        # ── Build streaming callback for real-time WebUI output ───────────
        # Emits throttled heading/progress updates so users see live activity
        # while tokens are being generated.
        stream_label = panelist_name or "War Room"
        _stream_char_count = 0
        _last_emit_at = 0.0
        _last_emit_chars = 0

        async def _stream_cb(chunk: str, full: str) -> str | None:
            nonlocal _stream_char_count, _last_emit_at, _last_emit_chars
            _stream_char_count += len(chunk)
            now = time.time()

            # Throttle UI updates to avoid flooding; still keeps it feeling live.
            if (_stream_char_count - _last_emit_chars) < 64 and (now - _last_emit_at) < 0.25:
                return None

            _last_emit_at = now
            _last_emit_chars = _stream_char_count

            try:
                if self.agent and self.agent.context:
                    status = f"🧠 {stream_label} generating… ({_stream_char_count} chars)"
                    war_log = getattr(self, "_war_log", None)
                    if war_log:
                        war_log.update(
                            heading=status,
                            update_progress="temporary",
                        )
                        preview_text = full if len(full) <= 900 else (full[:300] + "\n...\n" + full[-500:])
                        self._set_war_live_preview(
                            f"[{stream_label}] live output",
                            preview_text,
                        )
                    self.agent.context.log.set_progress(status, active=True)
            except Exception:
                pass  # Never let streaming display break the LLM call
            return None

        try:
            # Use cached war model if available; build fresh only as fallback
            llm = getattr(self, "_cached_war_llm", None)
            if llm is None:
                from plugins._model_config.helpers.model_config import (
                    build_war_model,
                    get_war_model_display,
                    get_config,
                )
                cfg = get_config(self.agent)
                war_cfg = cfg.get("war_model", {}) if isinstance(cfg, dict) else {}
                war_explicit = bool(
                    isinstance(war_cfg, dict)
                    and (war_cfg.get("provider") or war_cfg.get("name"))
                )
                war_display = get_war_model_display(self.agent)
                self._war_model_resolved = war_display
                llm = build_war_model(self.agent)
            else:
                # Determine if war model was explicitly configured
                try:
                    from plugins._model_config.helpers.model_config import get_config
                    cfg = get_config(self.agent)
                    war_cfg = cfg.get("war_model", {}) if isinstance(cfg, dict) else {}
                    war_explicit = bool(
                        isinstance(war_cfg, dict)
                        and (war_cfg.get("provider") or war_cfg.get("name"))
                    )
                except Exception:
                    pass

            response, _reasoning = await llm.unified_call(
                messages=messages,
                temperature=temperature,
                response_callback=_stream_cb,
            )
            return (response or "").strip()
        except Exception as exc:
            # Log fallback reason once so model misconfiguration is visible.
            self._war_fallback_count = int(getattr(self, "_war_fallback_count", 0)) + 1
            if not getattr(self, "_war_fallback_logged", False):
                self._war_fallback_logged = True
                detail = (
                    "War Room model call failed; falling back to Main model. "
                    f"Resolved war model: {war_display}. Error: {exc}"
                )
                try:
                    if self.agent and self.agent.context:
                        self.agent.context.log.log(type="warning", content=detail)
                except Exception:
                    pass
                if war_explicit:
                    PrintStyle(font_color="orange", padding=True).print(detail)

        try:
            raw, _ = await self.agent.call_chat_model(
                messages=messages,
                background=True,
            )
            return (raw or "").strip()
        except Exception as exc:
            return f"[LLM call error: {exc}]"

    async def _run_router(self, problem: str, mode_override: str) -> dict:
        """Run the complexity router; returns route dict with fallback defaults."""
        if mode_override in ("planning", "analysis", "execution"):
            mapping = {
                "planning":  {"complexity": "HIGH",   "agent_count": 5, "rounds": 3, "mode": "planning"},
                "analysis":  {"complexity": "MEDIUM", "agent_count": 3, "rounds": 2, "mode": "analysis"},
                "execution": {"complexity": "LOW",    "agent_count": 2, "rounds": 1, "mode": "execution"},
            }
            return mapping[mode_override]

        msgs = [
            SystemMessage(content=_ROUTER_SYSTEM),
            HumanMessage(content=f"Task to classify:\n{problem}"),
        ]
        raw = await self._llm_call(msgs, temperature=0.0)
        result = self._safe_json(raw)
        # validate and set sane defaults
        if result.get("agent_count") not in (1, 2, 3, 4, 5, 6):
            result["agent_count"] = 4
        if result.get("rounds") not in (1, 2, 3):
            result["rounds"] = 2
        if result.get("complexity") not in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "TRIVIAL"):
            result["complexity"] = "HIGH"
        return result

    async def _run_one_panelist(
        self,
        panelist: dict,
        problem: str,
        blackboard: list[dict],
        round_num: int,
        env_hints: list[str],
    ) -> dict:
        """Single panelist call; returns entry dict for the blackboard."""
        # Build blackboard snapshot for this panelist
        board_text = self._render_blackboard(blackboard)
        env_section = (
            "\n\nENVIRONMENT ISSUES DETECTED (consider in your response):\n"
            + "\n".join(f"- {h}" for h in env_hints)
        ) if env_hints else ""

        human_content = (
            f"TASK:\n{problem}"
            f"{env_section}"
            + (
                f"\n\nBLACKBOARD (all prior rounds):\n{board_text}"
                if board_text else
                "\n\n(You are first to speak — no prior discussion.)"
            )
            + (
                f"\n\nThis is Round {round_num}. "
                + ("Give your initial position." if round_num == 1
                   else (
                       "CRITICAL: You MUST reference a specific panelist by name "
                       "(e.g. 'STRATEGIST suggested X, but...'). "
                       "State what CHANGED in your position vs Round 1. "
                       "If nothing changed, explain WHY the same approach survives challenge. "
                       "Do NOT repeat your Round 1 response verbatim."
                   ))
            )
        )

        msgs = [
            SystemMessage(content=panelist["system"]),
            HumanMessage(content=human_content),
        ]
        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    msgs,
                    temperature=0.25,
                    panelist_name=panelist["name"],
                ),
                timeout=90,
            )
        except asyncio.TimeoutError:
            raw = f"[TIMEOUT: {panelist['name']} did not respond within 90s]"
            return {
                "agent":      panelist["name"],
                "role":       panelist["role"],
                "round":      round_num,
                "raw":        raw,
                "structured": {
                    "position": "timeout",
                    "suggested_action": "timeout",
                    "key_risk": "Panelist timed out after 90 seconds",
                    "confidence": 0.0,
                },
            }
        structured = self._safe_json(raw)

        return {
            "agent":      panelist["name"],
            "role":       panelist["role"],
            "round":      round_num,
            "raw":        raw,
            "structured": structured,
        }

    async def _run_parallel_round(
        self,
        panelists: list[dict],
        problem: str,
        blackboard: list[dict],
        round_num: int,
        env_hints: list[str],
        p_body: PrintStyle,
    ) -> list[dict]:
        """Fire all panelists in parallel; stream each result to WebUI as it arrives."""
        await self.set_progress(
            f"🧠 Round {round_num} — {len(panelists)} panelists firing in parallel..."
        )

        # Tagged wrapper bundles panelist identity with its result so we can
        # stream results to the WebUI in completion order (as_completed) without
        # a dict lookup (which fails because as_completed wraps futures).
        async def _tagged(p: dict) -> tuple[dict, dict]:
            try:
                entry = await self._run_one_panelist(
                    p, problem, blackboard, round_num, env_hints
                )
            except asyncio.TimeoutError:
                entry = {
                    "agent":      p["name"],
                    "role":       p["role"],
                    "round":      round_num,
                    "raw":        f"[TIMEOUT: {p['name']} did not respond within 90s]",
                    "structured": {
                        "position": "timeout",
                        "suggested_action": "timeout",
                        "key_risk": "Panelist timed out after 90 seconds",
                        "confidence": 0.0,
                    },
                }
            except Exception as exc:
                entry = {
                    "agent":      p["name"],
                    "role":       p["role"],
                    "round":      round_num,
                    "raw":        f"[ERROR: {exc}]",
                    "structured": {
                        "position": "error",
                        "suggested_action": "error",
                        "key_risk": str(exc),
                        "confidence": 0.0,
                    },
                }
            return p, entry

        tasks = [_tagged(p) for p in panelists]
        results: list[dict] = []

        # Stream each result to WebUI as it arrives (as_completed, not gather)
        for coro in asyncio.as_completed(tasks):
            panelist, entry = await coro

            results.append(entry)

            # ── Stream immediately to WebUI (not batch wait) ──────────────────
            s = entry["structured"]
            display = (
                f"[{entry['agent']}] {entry['role']}\n"
                f"  Position: {s.get('position', entry['raw'][:120])}\n"
                f"  Action:   {s.get('suggested_action', '—')}\n"
                f"  Risk:     {s.get('key_risk', '—')}\n"
                f"  Conf:     {s.get('confidence', '?')}"
            )
            p_body.print(display)

            self._clear_war_live_preview()
            self._append_war_section(
                (
                    f"[{entry['agent']}] {entry['role']} "
                    f"(conf: {s.get('confidence', '?')})\n"
                    f"  → Position: {s.get('position', entry['raw'][:120])}\n"
                    f"  → Action: {s.get('suggested_action', '—')}\n\n"
                ),
            )

            # Yield control so the WebSocket loop can push the update
            await asyncio.sleep(0)

        return results

    async def _run_flash_debate(
        self,
        panelists: list[dict],
        problem: str,
        blackboard: list[dict],
        consensus_score: float,
        p_body: PrintStyle,
    ) -> list[dict]:
        """Flash debate: identify 1-2 dissenters, fire them + 1 majority rep only."""
        all_entries = [e for rd in blackboard for e in rd.get("entries", [])]
        if not all_entries:
            return []

        # simple dissent detection: pick agents with lowest confidence
        sorted_entries = sorted(
            all_entries,
            key=lambda e: float(e.get("structured", {}).get("confidence", 0.5)),
        )
        dissenters = sorted_entries[:min(2, len(sorted_entries))]
        majority_entry = sorted_entries[-1] if len(sorted_entries) > 1 else sorted_entries[0]

        board_text = self._render_blackboard(blackboard)
        flash_entries: list[dict] = []
        tasks = []

        async def _flash_one(entry: dict, is_dissenter: bool) -> dict:
            agent_name = entry["agent"]
            panelist = next((p for p in panelists if p["name"] == agent_name), None)
            if not panelist:
                return {}
            prompt_prefix = (
                f"FLASH DEBATE — consensus_score={consensus_score:.2f}\n"
                + (
                    "You are a DISSENTER. In ≤40 words, state your final objection "
                    "and the specific consequence of ignoring your concern."
                    if is_dissenter else
                    "You represent the MAJORITY position. In ≤40 words, explain "
                    "why the group approach handles the dissent, or acknowledge it as a risk."
                )
            )
            msgs = [
                SystemMessage(content=panelist["system"]),
                HumanMessage(
                    content=f"{prompt_prefix}\n\nFULL BLACKBOARD:\n{board_text}\n\nTASK: {problem}"
                ),
            ]
            raw = await self._llm_call(msgs, temperature=0.2)
            return {
                "agent":      agent_name,
                "role":       panelist["role"],
                "round":      "flash",
                "raw":        raw,
                "structured": {"position": raw, "suggested_action": raw, "key_risk": "", "confidence": 0.5},
                "flash_role": "dissenter" if is_dissenter else "majority",
            }

        flash_tasks = [_flash_one(e, True)  for e in dissenters] + \
                      [_flash_one(majority_entry, False)]

        raw_results = await asyncio.gather(*flash_tasks, return_exceptions=True)
        for r in raw_results:
            if isinstance(r, dict) and r:
                flash_entries.append(r)
                role_label = r.get("flash_role", "")
                p_body.print(f"  ⚡ [{r['agent']}] ({role_label}): {r['raw'][:160]}")

        return flash_entries

    async def _run_synthesizer(
        self,
        problem: str,
        blackboard: list[dict],
        consensus_score: float,
        env_hints: list[str],
    ) -> str:
        """Always-called synthesizer; produces strict JSON decision object."""
        board_text = self._render_blackboard(blackboard)
        env_section = (
            "\n\nENVIRONMENT ISSUES TO ADDRESS IN REPAIR STEP:\n"
            + "\n".join(f"- {h}" for h in env_hints)
        ) if env_hints else ""

        human = (
            f"ORIGINAL TASK:\n{problem}"
            f"{env_section}"
            f"\n\nCONSENSUS SCORE AFTER DEBATE: {consensus_score:.2f}"
            f"\n\nFULL BLACKBOARD:\n{board_text}"
            "\n\nProduce the final JSON consensus decision now."
        )

        msgs = [
            SystemMessage(content=_SYNTHESIZER["system"]),
            HumanMessage(content=human),
        ]
        raw = await self._llm_call(msgs, temperature=0.1)

        # Stream synthesizer output into consolidated war log
        synthesis = self._safe_json(raw)
        self._clear_war_live_preview()
        self._append_war_section(
            (
                f"\n━━ SYNTHESIS ━━━━━━━━━━━━━━━\n"
                f"Consensus: \"{synthesis.get('consensus_action', raw[:200])}\"\n"
                f"Confidence: {synthesis.get('confidence', '?')}\n"
            ),
        )
        return raw

    # ── utilities ─────────────────────────────────────────────────────────────

    def _render_blackboard(self, blackboard: list[dict]) -> str:
        """Render blackboard as readable text for prompt injection."""
        lines: list[str] = []
        for rd in blackboard:
            r = rd["round"]
            lines.append(f"\n--- Round {r} ---")
            for entry in rd.get("entries", []):
                s = entry.get("structured", {})
                lines.append(
                    f"[{entry['agent']}] "
                    f"Position: {s.get('position', entry.get('raw','')[:100])} | "
                    f"Action: {s.get('suggested_action','—')} | "
                    f"Risk: {s.get('key_risk','—')} | "
                    f"Conf: {s.get('confidence','?')}"
                )
        return "\n".join(lines).strip()

    def _compute_consensus(self, entries: list[dict]) -> float:
        """Normalized token overlap with confidence boost. Returns 0-1."""
        _STOP_WORDS = frozenset({
            "the", "a", "to", "and", "or", "of", "in", "for",
            "with", "using", "via", "is", "it", "be", "on", "at",
            "by", "an", "as", "do", "if", "no", "not", "but",
            "this", "that", "from", "are", "was", "were", "been",
            "will", "can", "has", "have", "had", "should", "would",
        })

        actions = [
            e.get("structured", {}).get("suggested_action", e.get("raw", ""))
            for e in entries
        ]
        if len(actions) < 2:
            return 1.0

        def tok(text: str) -> set[str]:
            words = set(re.findall(r"\b\w+\b", text.lower()))
            return {w for w in words if len(w) >= 4 and w not in _STOP_WORDS}

        sets = [tok(a) for a in actions]
        scores: list[float] = []
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                a, b = sets[i], sets[j]
                if not a and not b:
                    scores.append(1.0)
                elif not a or not b:
                    scores.append(0.0)
                else:
                    # Overlap coefficient: |A∩B| / min(|A|,|B|)
                    scores.append(len(a & b) / min(len(a), len(b)))

        base_score = sum(scores) / len(scores) if scores else 1.0

        # Confidence proximity boost: if all panelists within 0.15, add 0.2
        confidences = [
            float(e.get("structured", {}).get("confidence", 0.5))
            for e in entries
        ]
        if confidences and (max(confidences) - min(confidences)) <= 0.15:
            base_score += 0.2

        return min(base_score, 1.0)

    def _safe_json(self, raw: str) -> dict:
        """Extract JSON from a string; returns {} on failure."""
        if not raw:
            return {}
        try:
            # strip markdown fences
            cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
            # find the first { ... } block
            start = cleaned.find("{")
            end   = cleaned.rfind("}")
            if start >= 0 and end >= 0:
                return json.loads(cleaned[start : end + 1])
        except Exception:
            pass
        try:
            from helpers.dirty_json import DirtyJson
            return DirtyJson.parse_string(raw) or {}
        except Exception:
            pass
        return {}

    def _refresh_war_log_content(self) -> None:
        war_log = getattr(self, "_war_log", None)
        if not war_log:
            return

        sections = list(getattr(self, "_war_live_sections", []))
        preview = getattr(self, "_war_live_preview", "")
        content = "".join(sections)
        if preview:
            content += f"\n━━ Live Generation ━━━━━━━━━━━━━━━\n{preview.strip()}\n"
        war_log.update(content=content)

    def _append_war_section(self, text: str) -> None:
        sections = getattr(self, "_war_live_sections", None)
        if sections is None:
            self._war_live_sections = []
            sections = self._war_live_sections
        sections.append(text)
        self._refresh_war_log_content()

    def _set_war_live_preview(self, title: str, body: str = "") -> None:
        body = (body or "").strip()
        self._war_live_preview = title if not body else f"{title}\n{body}"
        self._refresh_war_log_content()

    def _clear_war_live_preview(self) -> None:
        self._war_live_preview = ""
        self._refresh_war_log_content()

    def _format_compact_result(
        self,
        synthesis: dict,
        synthesis_raw: str,
        elapsed: float,
        panelists: list[dict],
        round_count: int,
    ) -> str:
        """Build a compact result for the agent's message history (~200-400 tokens)."""
        faz = synthesis.get("for_agent_zero", {})

        risks = synthesis.get("key_risks", [])
        risks_str = "; ".join(risks) if risks else "none identified"

        dissent = synthesis.get("dissent_notes", [])
        dissent_str = "; ".join(dissent) if dissent else "none"

        lines = [
            f"# War Room Consensus ({elapsed:.1f}s, {len(panelists)} panelists, {round_count} rounds)",
            "",
            f"**Consensus:** {synthesis.get('consensus_action', 'See synthesis below')}",
            f"**Confidence:** {synthesis.get('confidence', '?')}",
            f"**Key Risks:** {risks_str}",
            f"**Dissent:** {dissent_str}",
        ]

        if faz:
            lines += [
                "",
                "## FOR_AGENT_ZERO:",
                "```json",
                json.dumps(faz, indent=2),
                "```",
                "Execute the recommended tool call above immediately.",
            ]
        else:
            lines += ["```", synthesis_raw[:600], "```"]

        return "\n".join(lines)

    def _format_verbose_log(
        self,
        blackboard: list[dict],
        synthesis: dict,
        synthesis_raw: str,
        elapsed: float,
        panelists: list[dict],
        round_count: int,
    ) -> str:
        """Build the full verbose transcript for WebUI display only."""
        faz = synthesis.get("for_agent_zero", {})
        lines = [
            f"# 🏛️ War Room Complete — {elapsed:.1f}s | {round_count} round(s) | {len(panelists)} panelists",
            "",
            f"**Consensus:** {synthesis.get('consensus_action', 'See synthesis below')}",
            f"**Confidence:** {synthesis.get('confidence', '?')}",
            f"**War Model:** {getattr(self, '_war_model_resolved', 'unknown')}",
        ]

        fallback_count = int(getattr(self, "_war_fallback_count", 0))
        if fallback_count > 0:
            lines.append(f"**War Model Fallbacks To Main:** {fallback_count}")

        risks = synthesis.get("key_risks", [])
        if risks:
            lines += ["", "**Key Risks:**"] + [f"- {r}" for r in risks]

        dissent = synthesis.get("dissent_notes", [])
        if dissent:
            lines += ["", "**Dissent Notes:**"] + [f"- {d}" for d in dissent]

        lines += [
            "",
            "---",
            "## Debate Transcript",
        ]
        for rd in blackboard:
            r = rd["round"]
            lines.append(f"\n### Round {r}")
            for e in rd.get("entries", []):
                s = e.get("structured", {})
                lines.append(
                    f"**[{e['agent']}]** {s.get('position', e.get('raw','')[:120])}  "
                    f"→ _{s.get('suggested_action','—')}_"
                )

        lines += [
            "",
            "---",
            "## ✅ SYNTHESIZER FINAL DECISION",
            "",
            f"**Reasoning:** {synthesis.get('reasoning_trace', '—')}",
            "",
        ]

        if faz:
            lines += [
                "## 🎯 FOR_AGENT_ZERO:",
                "```json",
                json.dumps(faz, indent=2),
                "```",
            ]
        else:
            lines += ["```", synthesis_raw[:1200], "```"]

        return "\n".join(lines)

    def get_log_object(self) -> Any:
        import uuid

        pre_id = str(uuid.uuid4())
        return self.agent.context.log.log(
            type="tool",
            heading=f"🏛️ {self.agent.agent_name} — War Room Thinking",
            content="Initializing War Room...",
            kvps=self.args,
            _tool_name=self.name,
            id=pre_id,
        )

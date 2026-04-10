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
import hashlib
import json
import os
import re
import time
import uuid
from collections import OrderedDict
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
            '{"position":"1-sentence position",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"specific next action",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"top risk","confidence":0.8,"is_novel":"yes or no"}'
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
            '{"position":"1-sentence challenge",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"alternative action",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"what group is missing","confidence":0.8,"is_novel":"yes or no"}'
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
            '{"position":"implementation approach",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"exact tool or command",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"top execution risk","confidence":0.8,"is_novel":"yes or no"}'
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
            '{"position":"key knowledge for this task",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"research-backed recommendation",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"known pitfall from prior art","confidence":0.8,"is_novel":"yes or no"}'
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
            '{"position":"quality concern",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"test or verification step",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"what could fail silently","confidence":0.8,"is_novel":"yes or no"}'
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
            '{"position":"adversarial assessment",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"highest-impact attack path",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"detection or mitigation risk","confidence":0.8,"is_novel":"yes or no"}'
        ),
    },
    {
        "name": "PENTEST_SPECIALIST",
        "role": "Offensive Security Expert",
        "icon": "icon://security",
        "color": "#e74c3c",
        "system": (
            "You are PENTEST_SPECIALIST on a war room panel. "
            "Focus: kill chain, initial foothold, lateral movement, persistence, "
            "MITRE ATT&CK techniques. "
            "Always propose the most direct path to objective. "
            "Think like a red team operator with 15 years of experience.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"1-sentence offensive assessment",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"specific attack path",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"detection or mitigation risk","confidence":0.8,"is_novel":"yes or no"}'
        ),
    },
    {
        "name": "DEFENDER",
        "role": "Blue Team Architect",
        "icon": "icon://shield",
        "color": "#3498db",
        "system": (
            "You are DEFENDER on a war room panel. "
            "Focus: what artifacts will our actions leave, what will defenders see, "
            "detection engineering, SIEM rules, EDR signatures. "
            "Force adversarial actions to be evasion-aware.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"defensive perspective",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"evasion-aware recommendation",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"detection artifact or alert","confidence":0.8,"is_novel":"yes or no"}'
        ),
    },
    {
        "name": "CLOUD_ARCHITECT",
        "role": "Cloud Security Specialist",
        "icon": "icon://cloud",
        "color": "#1abc9c",
        "system": (
            "You are CLOUD_ARCHITECT on a war room panel. "
            "Focus: AWS/GCP/Azure service abuse, IAM misconfiguration, "
            "blast radius assessment, cloud-native attack paths, "
            "metadata services, storage misconfigs, serverless exploitation.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"cloud security assessment",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"cloud-native approach",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"blast radius or detection risk","confidence":0.8,"is_novel":"yes or no"}'
        ),
    },
    {
        "name": "CODE_AUDITOR",
        "role": "Source Code Review Expert",
        "icon": "icon://code",
        "color": "#9b59b6",
        "system": (
            "You are CODE_AUDITOR on a war room panel. "
            "Focus: data flow analysis, trust boundaries, deserialization paths, "
            "taint analysis, injection sinks, authentication bypass patterns. "
            "Always trace data from source to sink.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"code-level finding",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"specific code path to investigate",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"exploitation complexity or false positive risk","confidence":0.8,"is_novel":"yes or no"}'
        ),
    },
    {
        "name": "DEBUGGER",
        "role": "Systems Debugging Expert",
        "icon": "icon://bug_report",
        "color": "#e67e22",
        "system": (
            "You are DEBUGGER on a war room panel. "
            "Focus: root cause analysis, reproduction steps, minimal repro cases, "
            "tracing, profiling, stack analysis, race condition detection. "
            "Always start with the simplest hypothesis.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"diagnostic assessment",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"specific debugging step",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"misdiagnosis risk","confidence":0.8,"is_novel":"yes or no"}'
        ),
    },
    {
        "name": "REVERSE_ENGINEER",
        "role": "Binary Analysis Expert",
        "icon": "icon://memory",
        "color": "#95a5a6",
        "system": (
            "You are REVERSE_ENGINEER on a war room panel. "
            "Focus: binary analysis, firmware, protocol reverse engineering, "
            "Ghidra/Frida/Wireshark workflows, pattern identification, "
            "unpacking, deobfuscation, dynamic instrumentation.\n\n"
            "Respond ONLY with this JSON (no prose):\n"
            '{"position":"reverse engineering assessment",'
            '"idea_class":"one of: tool_invocation|technique|research|exploit|bypass|detection|remediation|investigation|unknown",'
            '"suggested_action":"specific RE approach",'
            '"action_target":"what system/file/service this acts on",'
            '"prerequisites":["what must be true first"],'
            '"key_risk":"analysis complexity or anti-analysis measures","confidence":0.8,"is_novel":"yes or no"}'
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
#  DOMAIN-ADAPTIVE SPECIALIST CLASSIFIER (Upgrade 6)
# ─────────────────────────────────────────────────────────────────────────────
_DOMAIN_MAP: dict[str, list[str]] = {
    r"web|http|api|jwt|cookie|session|xss|sqli|csrf": ["PENTEST_SPECIALIST", "CODE_AUDITOR"],
    r"cloud|aws|gcp|azure|s3|iam|lambda|kubernetes|k8s": ["CLOUD_ARCHITECT", "PENTEST_SPECIALIST"],
    r"binary|firmware|elf|pe32|ghidra|frida|assembly|disassembl": ["REVERSE_ENGINEER", "EXECUTOR"],
    r"debug|traceback|exception|crash|segfault|core.dump": ["DEBUGGER", "CODE_AUDITOR"],
    r"detect|alert|siem|log|artifact|edr|sigma|yara": ["DEFENDER", "CRITIC"],
}


def _classify_domain(problem: str) -> list[str]:
    """Return list of specialist panelist names suited for this problem domain."""
    specialists: list[str] = []
    for pattern, names in _DOMAIN_MAP.items():
        if re.search(pattern, problem, re.IGNORECASE):
            for n in names:
                if n not in specialists:
                    specialists.append(n)
    return specialists[:2]  # max 2 specialists

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
        "TODO PLAN RULES:\n"
        "In ADDITION to for_agent_zero, include a \"todo_plan\" array listing ALL action items\n"
        "discovered during the war room session. Each item is an object with:\n"
        '  {"title": "...", "description": "...", "priority": "high|normal|low",\n'
        '   "subtasks": [{"title": "...", "description": "..."}]}\n'
        "- The first item in todo_plan should correspond to for_agent_zero (the immediate next step).\n"
        "- Subsequent items are follow-up steps in execution order.\n"
        "- priority: \"high\" for critical/blocking, \"normal\" for standard, \"low\" for nice-to-have.\n"
        "- subtasks is optional — include only when a task has clear sub-steps.\n"
        "- Be specific and actionable — no vague items like \"investigate further\".\n\n"
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

# ─────────────────────────────────────────────────────────────────────────────
#  HEURISTIC FAST-PATH ROUTER PATTERNS (Upgrade 5)
# ─────────────────────────────────────────────────────────────────────────────
_TRIVIAL_PATTERNS = [
    re.compile(r"\b(ls|cat|pwd|echo|grep|find|head|tail|which|whoami|hostname)\b", re.I),
    re.compile(r"what is \w+", re.I),
    re.compile(r"read (the |this )?(file|content)", re.I),
    re.compile(r"list (files|directory|dir)", re.I),
    re.compile(r"print.{0,30}variable", re.I),
    re.compile(r"^(show|display|get|check) ", re.I),
]

_CRITICAL_PATTERNS = [
    re.compile(r"\b(zero.?day|0day|RCE|remote code|privilege escalat|initial access)\b", re.I),
    re.compile(r"\b(attack (chain|surface|vector)|threat model|red team|APT)\b", re.I),
    re.compile(r"\b(architecture|system design|multi.?stage)\b", re.I),
    re.compile(r"(full|complete) (pentest|assessment|audit|compromise)", re.I),
]

_HIGH_PATTERNS = [
    re.compile(r"\b(CVE-\d{4}-\d+|sql.?inject|XSS|SSRF|XXE|deseri|buffer overflow)\b", re.I),
    re.compile(r"\b(vulnerabilit|exploit|bypass|escalat|inject)\b", re.I),
    re.compile(r"\b(debug|diagnose|traceback|exception|error|crash)\b", re.I),
    re.compile(r"\b(refactor|redesign|migrate|integrate)\b", re.I),
]

_ROUTER_CACHE_MAX = 20  # max cached router results

# ─────────────────────────────────────────────────────────────────────────────
#  LATERAL THINKING SEEDS (Upgrade 4) — Unstick mechanism
# ─────────────────────────────────────────────────────────────────────────────
_LATERAL_SEEDS: list[tuple[str, str]] = [
    ("INVERSION",
     "Assume every approach explored so far is completely wrong. "
     "What would be true? What approach would work in that world?"),
    ("ANALOGICAL",
     "This problem is similar to something in a completely unrelated domain "
     "(physical security, supply chain, social engineering, hardware, business logic). "
     "What techniques from that domain could apply?"),
    ("CONSTRAINT_REMOVAL",
     "If there were no restrictions — no detection risk, no time limit, "
     "unlimited access — what would you try?"),
    ("MINIMUM_FOOTPRINT",
     "What is the absolute smallest, simplest possible action "
     "that could give us meaningful information or progress?"),
    ("FAILURE_ANALYSIS",
     "What would cause every approach we've tried to fail simultaneously? "
     "What's the common dependency we're missing?"),
]

# ─────────────────────────────────────────────────────────────────────────────
#  SYNTHESIZER OUTPUT VALIDATION CONSTANTS (Upgrade 11)
# ─────────────────────────────────────────────────────────────────────────────
_VALID_TOOL_NAMES = {
    "code_execution", "browser_open", "browser_do", "search_engine",
    "document_query", "skills_tool", "call_subordinate",
    "memory_tool", "response", "think", "todo",
}

_TOOL_REQUIRED_ARGS = {
    "code_execution": ["runtime", "code"],
    "browser_open":   ["url"],
    "browser_do":     ["action"],
    "search_engine":  ["query"],
    "skills_tool":    ["action"],
    "memory_tool":    ["action"],
    "call_subordinate": ["message"],
    "response":       ["text"],
}

# ─────────────────────────────────────────────────────────────────────────────
#  NOVELTY-BASED TERMINATION CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
DEAD_END_THRESHOLD = 3      # consecutive stale rounds to confirm dead end
SAFETY_CEILING = 25         # absolute maximum rounds
NOVELTY_FLOOR = 0.08        # minimum fraction of new ideas per round
TIME_BUFFER_SECONDS = 15    # seconds reserved for synthesis when time budget active

# Concurrent session lock for war model cache (Upgrade 23)
_WAR_LLM_LOCK = asyncio.Lock()


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
#  PROGRESSIVE DEPTH ROUND ARCHITECTURE (Upgrade 7)
# ─────────────────────────────────────────────────────────────────────────────
_PHASE_PROMPTS = {
    "explore": (
        "EXPLORE PHASE — Round {n}.\n"
        "This is ideation. Be creative and broad. Name ALL viable approaches "
        "you can think of. Don't commit to one — scan the entire solution space. "
        "Aim for novelty: what approach would someone miss on first reading?\n"
        "Include: unconventional paths, non-obvious dependencies, things that "
        "look impossible but might not be."
    ),
    "debate": (
        "DEBATE PHASE — Round {n}.\n"
        "The group has had its opening positions. Now CHALLENGE and REFINE.\n"
        "MANDATORY: Name at least one other panelist by role (e.g. 'STRATEGIST's "
        "approach has a flaw:...'). Explain what changed in your assessment "
        "and WHY. If you agree, say what specifically convinced you. "
        "If you disagree, propose a concrete alternative — not a vague objection."
    ),
    "deep_dive": (
        "DEEP DIVE PHASE — Round {n}.\n"
        "A leading approach has emerged. Now find its edges and failure modes.\n"
        "Focus: What breaks this? What's the hardest part to execute? "
        "What assumption, if wrong, would make the whole approach fail? "
        "What does success look like exactly — what's the verification step?"
    ),
    "edge_hunt": (
        "EDGE HUNT PHASE — Round {n}.\n"
        "We've explored the main paths. Hunt for the non-obvious.\n"
        "Look at: race conditions, timing attacks, encoding edge cases, "
        "language/platform quirks, dependency chains, business logic bypass, "
        "second-order effects. What have we collectively been too focused to notice?"
    ),
}


def _get_phase(round_num: int) -> str:
    """Map round number to exploration phase."""
    if round_num == 1:
        return "explore"
    if round_num <= 3:
        return "debate"
    if round_num <= 6:
        return "deep_dive"
    return "edge_hunt"


# ─────────────────────────────────────────────────────────────────────────────
#  ADAPTIVE TEMPERATURE SCHEDULE (Upgrade 9)
# ─────────────────────────────────────────────────────────────────────────────
_TEMPERATURE_MAP = {
    # (phase, role) → temperature
    ("explore",    "STRATEGIST"):   0.75,
    ("explore",    "CHALLENGER"):   0.80,
    ("explore",    "EXECUTOR"):     0.50,
    ("explore",    "RESEARCHER"):   0.65,
    ("explore",    "CRITIC"):       0.60,
    ("explore",    "TACTICIAN"):    0.85,
    ("debate",     "*"):            0.35,
    ("deep_dive",  "*"):            0.20,
    ("edge_hunt",  "*"):            0.45,
    ("synthesis",  "SYNTHESIZER"):  0.05,
    ("router",     "*"):            0.00,
}


def _get_temperature(phase: str, role: str) -> float:
    """Get temperature for a (phase, role) pair with wildcard fallback."""
    return (
        _TEMPERATURE_MAP.get((phase, role))
        or _TEMPERATURE_MAP.get((phase, "*"))
        or 0.25  # fallback
    )


# ─────────────────────────────────────────────────────────────────────────────
#  IDEA REGISTRY — Semantic Deduplication for Exploration Tracking
# ─────────────────────────────────────────────────────────────────────────────
_STOP_WORDS = frozenset({
    "the", "a", "to", "and", "or", "of", "in", "for", "with", "using",
    "via", "is", "it", "be", "on", "at", "by", "an", "as", "do", "if",
    "no", "not", "but", "this", "that", "from", "are", "was", "were",
    "been", "will", "can", "has", "have", "had", "should", "would",
    "could", "may", "might", "shall", "must", "use", "also", "just",
    "like", "more", "need", "want", "make", "then", "than", "very",
    "most", "some", "other", "each", "every", "all", "any", "such",
})


class IdeaRegistry:
    """Tracks explored ideas with semantic fingerprinting for deduplication."""

    def __init__(self) -> None:
        self.ideas: dict[str, dict] = {}                # fingerprint → full entry
        self.explored_angles: list[str] = []             # short labels of explored approaches
        self.pending_angles: list[str] = []              # angles mentioned but not yet pursued
        self.blocked_angles: dict[str, str] = {}         # angle → reason ruled out
        self.idea_endorsements: dict[str, int] = {}      # fingerprint → endorsement count
        self._round_novel_counts: list[int] = []         # novel idea count per round
        self._fp_sets: dict[str, set[str]] = {}          # fingerprint → keyword set (fast lookup)

    @staticmethod
    def _fingerprint(text: str, structured: dict | None = None) -> str:
        """Extract 6-8 high-information keywords, sort deterministically, return as key.

        If structured data contains idea_class or action_target, include them
        in keyword extraction for more precise deduplication (Upgrade 20).
        """
        extra = ""
        if structured:
            ic = structured.get("idea_class", "")
            at = structured.get("action_target", "")
            if ic and ic != "unknown":
                extra += f" {ic}"
            if at:
                extra += f" {at}"

        words = re.findall(r"[a-zA-Z]{4,}", (text + extra).lower())
        keywords = [w for w in words if w not in _STOP_WORDS]
        # Take up to 8 most unique keywords (first occurrences preserve salience)
        seen: set[str] = set()
        unique: list[str] = []
        for w in keywords:
            if w not in seen:
                seen.add(w)
                unique.append(w)
            if len(unique) >= 8:
                break
        return ",".join(sorted(unique))

    @staticmethod
    def _fp_keywords(fp: str) -> set[str]:
        """Extract keyword set from a fingerprint string."""
        return set(fp.split(",")) if fp else set()

    def is_novel(self, text: str) -> bool:
        """Check if text has >65% keyword overlap with any existing idea."""
        candidate_kw = set(re.findall(r"[a-zA-Z]{4,}", text.lower()))
        candidate_kw = {w for w in candidate_kw if w not in _STOP_WORDS}
        if not candidate_kw:
            return True

        for fp, existing_kw in self._fp_sets.items():
            if not existing_kw:
                continue
            overlap = len(candidate_kw & existing_kw) / max(min(len(candidate_kw), len(existing_kw)), 1)
            if overlap > 0.65:
                return False
        return True

    def register_and_count_novel(self, round_entries: list[dict]) -> int:
        """Register entries from a round. Returns count of genuinely new ideas."""
        novel_count = 0
        for entry in round_entries:
            s = entry.get("structured", {})
            text = f"{s.get('suggested_action', '')} {s.get('position', '')}"
            fp = self._fingerprint(text, structured=s)

            if self.is_novel(text):
                self.ideas[fp] = entry
                self._fp_sets[fp] = self._fp_keywords(fp)
                self.idea_endorsements[fp] = 1
                label = s.get("suggested_action", text[:80])
                if label and label not in self.explored_angles:
                    self.explored_angles.append(label[:120])
                novel_count += 1
            else:
                # Find matching fingerprint and increment endorsement
                candidate_kw = set(re.findall(r"[a-zA-Z]{4,}", text.lower()))
                candidate_kw = {w for w in candidate_kw if w not in _STOP_WORDS}
                best_match = None
                best_overlap = 0.0
                for existing_fp, existing_kw in self._fp_sets.items():
                    if not existing_kw:
                        continue
                    overlap = len(candidate_kw & existing_kw) / max(min(len(candidate_kw), len(existing_kw)), 1)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_match = existing_fp
                if best_match:
                    self.idea_endorsements[best_match] = self.idea_endorsements.get(best_match, 1) + 1

        self._round_novel_counts.append(novel_count)
        return novel_count

    def get_unexplored_angles(self) -> list[str]:
        """Return pending angles not yet pursued."""
        return list(self.pending_angles)

    def get_most_endorsed_action(self) -> str:
        """Return the suggested_action with the highest endorsement count."""
        if not self.idea_endorsements:
            return ""
        best_fp = max(self.idea_endorsements, key=self.idea_endorsements.get)  # type: ignore[arg-type]
        entry = self.ideas.get(best_fp, {})
        return entry.get("structured", {}).get("suggested_action", "")

    def get_top_ideas(self, n: int = 5) -> list[dict]:
        """Return top N ideas by endorsement count."""
        sorted_fps = sorted(self.idea_endorsements, key=self.idea_endorsements.get, reverse=True)  # type: ignore[arg-type]
        return [self.ideas[fp] for fp in sorted_fps[:n] if fp in self.ideas]

    def get_summary(self) -> str:
        """Return structured summary of exploration state."""
        top = self.get_top_ideas(5)
        top_lines = []
        for idea in top:
            s = idea.get("structured", {})
            fp_key = self._fingerprint(f"{s.get('suggested_action', '')} {s.get('position', '')}")
            endorsements = self.idea_endorsements.get(fp_key, 1)
            top_lines.append(f"  [{endorsements}x] {s.get('suggested_action', '?')[:100]}")

        lines = [
            f"Ideas explored: {len(self.ideas)}",
            "Top ideas (by endorsement):",
        ] + (top_lines or ["  (none)"]) + [
            f"Pending angles: {self.pending_angles or '(none)'}",
            f"Blocked angles: {dict(self.blocked_angles) or '(none)'}",
            f"Novel counts per round: {self._round_novel_counts}",
        ]
        return "\n".join(lines)

    def novelty_ratio(self, round_entries: list[dict]) -> float:
        """Return new_idea_count / max(len(round_entries), 1) without registering."""
        count = 0
        for entry in round_entries:
            s = entry.get("structured", {})
            text = f"{s.get('suggested_action', '')} {s.get('position', '')}"
            if self.is_novel(text):
                count += 1
        return count / max(len(round_entries), 1)

    def add_branch_approaches(self, approaches: list[str]) -> None:
        """Add approaches to explored_angles."""
        for a in approaches:
            if a not in self.explored_angles:
                self.explored_angles.append(a[:120])


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
        time_budget_seconds: int = 0,
        **kwargs: Any,
    ) -> Response:

        # ── resolve args ──────────────────────────────────────────────────────
        if not problem:
            problem = self.args.get("problem", "") or self.message
        error_context = error_context or self.args.get("error_context", "")
        mode_override = mode or self.args.get("mode", "")

        # ── Session ID for traceability (Upgrade 23) ──────────────────────────
        self._session_id = f"warroom_{uuid.uuid4().hex[:8]}"

        # ── Fast mode shortcut (Upgrade 21) ───────────────────────────────────
        if mode_override == "fast":
            time_budget_seconds = time_budget_seconds or 45

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

        # ── Cache war model on agent context (Upgrade 19) ────────────
        # Tool instances are ephemeral; caching on self.* is per-call only.
        # Use agent context attrs so the model persists across war room calls.
        ctx = self.agent.context
        _war_llm_cache_key = "_war_llm_instance"
        _war_display_key = "_war_model_display"
        _war_config_hash_key = "_war_llm_config_hash"

        self._cached_war_llm = None
        try:
            from plugins._model_config.helpers.model_config import (
                build_war_model,
                get_war_model_display,
                get_config,
            )
            cfg = get_config(self.agent)
            war_cfg = cfg.get("war_model", {}) if isinstance(cfg, dict) else {}
            config_fingerprint = hashlib.md5(
                json.dumps(war_cfg, sort_keys=True, default=str).encode()
            ).hexdigest()

            async with _WAR_LLM_LOCK:
                cached_fingerprint = getattr(ctx, _war_config_hash_key, None)

                if cached_fingerprint != config_fingerprint or not hasattr(ctx, _war_llm_cache_key):
                    setattr(ctx, _war_llm_cache_key, build_war_model(self.agent))
                    setattr(ctx, _war_display_key, get_war_model_display(self.agent))
                    setattr(ctx, _war_config_hash_key, config_fingerprint)

                self._cached_war_llm = getattr(ctx, _war_llm_cache_key)
                self._war_model_resolved = getattr(ctx, _war_display_key, "unknown")
        except Exception:
            self._cached_war_llm = None

        start_time = time.time()

        # ── Time budget helper (Upgrade 21) ───────────────────────────────────
        def _time_remaining() -> float:
            if not time_budget_seconds:
                return float("inf")
            return time_budget_seconds - (time.time() - start_time)

        # ── Step 0: Complexity Router ─────────────────────────────────────────
        await self.set_progress("⚡ Complexity Router classifying task...")
        route = await self._run_router(problem, mode_override)
        agent_count: int = route.get("agent_count", 4)
        max_rounds:  int = route.get("rounds", 2)
        complexity:  str = route.get("complexity", "HIGH")
        task_mode:   str = route.get("mode", "planning")

        # ── Fast mode override (Upgrade 21) ───────────────────────────────────
        if mode_override == "fast":
            route = {"complexity": "LOW", "agent_count": 2, "rounds": 2, "mode": "execution"}
            agent_count = 2
            max_rounds = 2
            complexity = "LOW"
            task_mode = "execution"
            time_budget_seconds = time_budget_seconds or 45

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

        # ── Domain-Adaptive Specialist Injection (Upgrade 6) ──────────────────
        domain_specialists = _classify_domain(problem)
        if domain_specialists and agent_count >= 3:
            all_panelist_map = {p["name"]: p for p in _ALL_PANELISTS}
            for specialist_name in domain_specialists:
                if specialist_name not in [p["name"] for p in panelists]:
                    if specialist_name in all_panelist_map:
                        # Replace the last non-essential panelist
                        if len(panelists) > 2:
                            panelists[-1] = all_panelist_map[specialist_name]
                        else:
                            panelists.append(all_panelist_map[specialist_name])

        # ── Single consolidated log entry for the entire War Room session ─────
        # Reuse the active tool log when available so live updates appear in the
        # same UI tab the user already has open.
        self._war_log = getattr(self, "log", None)
        if not self._war_log:
            # Extension or direct call — create a proper tool log so the WebUI
            # routes it through drawMessageWarRoom (requires type="tool" +
            # _tool_name="think" in kvps).
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

        # Session traceability (Upgrade 23)
        self._append_war_section(f"Session: {self._session_id}\n")

        # ── Blackboard ────────────────────────────────────────────────────────
        blackboard: list[dict] = []   # list of round dicts
        consensus_score: float = 0.0

        # ── Cross-Session Memory (Upgrade 12) ─────────────────────────────────
        historical_ctx = await self._load_historical_context(problem)
        if historical_ctx:
            blackboard.append({
                "round": "historical",
                "entries": [{
                    "agent": "MEMORY",
                    "role": "Historical Context",
                    "round": "historical",
                    "raw": historical_ctx,
                    "structured": {
                        "position": "Insights from past War Room sessions",
                        "suggested_action": historical_ctx[:300],
                        "key_risk": "Historical context may be outdated",
                        "confidence": 0.5,
                    },
                }],
            })
            self._append_war_section(f"\n📚 Historical context loaded ({len(historical_ctx)} chars)\n")

        # ── Research Enrichment Pre-Pass ──────────────────────────────────────
        if complexity in ("CRITICAL", "HIGH") and not error_context:
            try:
                research_entries = await self._research_enrich(problem)
                if research_entries:
                    blackboard.append({"round": "research", "entries": research_entries})
                    self._append_war_section(f"\n🔍 Research enrichment: {len(research_entries)} results\n")
            except Exception as exc:
                self._append_war_section(f"\n⚠️ Research enrichment failed: {exc}\n")

        # ── Novelty-Based Exploration Loop ────────────────────────────────────
        idea_registry = IdeaRegistry()
        stale_streak = 0
        round_num = 0
        used_laterals: set[str] = set()
        dead_end_info: dict | None = None

        # For TRIVIAL/LOW, cap at the router's max_rounds
        effective_ceiling = max_rounds if complexity in ("TRIVIAL", "LOW") else SAFETY_CEILING

        # ── CRITICAL Decomposition Pre-Pass (Upgrade 22) ─────────────────────
        if complexity == "CRITICAL":
            self._append_war_section("\n🔬 CRITICAL decomposition pre-pass...\n")
            try:
                sub_problems = await self._run_decomposition(problem)
                if sub_problems:
                    sub_tasks = [
                        self._run_mini_warroom(sp, agent_count=2, rounds=1)
                        for sp in sub_problems[:5]
                    ]
                    sub_results = await asyncio.gather(*sub_tasks, return_exceptions=True)
                    decomp_entries = []
                    for i, result in enumerate(sub_results):
                        if isinstance(result, dict):
                            decomp_entries.append({
                                "agent": f"DECOMP_{i+1}",
                                "role": f"Sub-problem: {sub_problems[i][:40]}",
                                "round": "decomposition",
                                "raw": str(result),
                                "structured": result,
                            })
                    if decomp_entries:
                        blackboard.append({"round": "decomposition", "entries": decomp_entries})
                        self._append_war_section(f"📋 {len(decomp_entries)} sub-problems analyzed\n")
            except Exception as exc:
                self._append_war_section(f"⚠️ Decomposition failed: {exc}\n")

        # ── Speculative synthesis variable (Upgrade 10) ───────────────────────
        speculative_synth_task = None

        while stale_streak < DEAD_END_THRESHOLD and round_num < effective_ceiling:
            # ── Time Budget Check (Upgrade 21) ────────────────────────────────
            if _time_remaining() < TIME_BUFFER_SECONDS:
                self._append_war_section(
                    f"\n⏱️ Time budget approaching ({time_budget_seconds}s) — "
                    f"collapsing to synthesis after {round_num} rounds\n"
                )
                break

            round_num += 1

            p_div.print(f"\n{'═'*55}\n  ROUND {round_num} (ceiling: {effective_ceiling})\n{'═'*55}")

            self._append_war_section(
                f"\n━━ Round {round_num} ━━━━━━━━━━━━━━━━━\n",
            )

            # ── Lateral Thinking Injection (Upgrade 4) ─────────────────────────
            lateral_prompt = ""
            if stale_streak == 2:
                lateral_prompt = self._build_lateral_injection(stale_streak, used_laterals) or ""
                if lateral_prompt:
                    stale_streak = 1  # partial reset — give it one more shot
                    self._append_war_section(f"\n🔀 Lateral thinking injected\n")

            round_entries = await self._run_parallel_round(
                panelists=panelists,
                problem=problem,
                blackboard=blackboard,
                round_num=round_num,
                env_hints=env_hints,
                p_body=p_body,
                idea_registry=idea_registry,
                lateral_prompt=lateral_prompt,
            )
            blackboard.append({"round": round_num, "entries": round_entries})

            # ── Novelty tracking ──────────────────────────────────────────────
            new_idea_count = idea_registry.register_and_count_novel(round_entries)
            novelty_ratio = new_idea_count / max(len(round_entries), 1)

            # ── Divergence Detection ──────────────────────────────────────────
            consensus_score = self._compute_consensus(round_entries)
            p_body.print(
                f"📐 Round {round_num}: consensus={consensus_score:.2f}, "
                f"novelty={novelty_ratio:.2f} ({new_idea_count} new), "
                f"stale_streak={stale_streak}"
            )
            self._append_war_section(
                f"\n📐 Consensus: {consensus_score:.2f} | "
                f"Novelty: {novelty_ratio:.2f} ({new_idea_count} new) | "
                f"Stale streak: {stale_streak}\n",
            )

            if novelty_ratio < NOVELTY_FLOOR:
                stale_streak += 1
            else:
                stale_streak = 0

            # Consensus is logged but NOT used as a stop signal
            if consensus_score >= 0.70:
                p_body.print("✅ Consensus reached — continuing if novel ideas emerging")
                self._append_war_section("✅ Consensus reached (continuing exploration)\n")

            if consensus_score < 0.50:
                # escalate: add CRITIC if not already present
                if not any(p["name"] == "CRITIC" for p in panelists):
                    critic = next(p for p in _ALL_PANELISTS if p["name"] == "CRITIC")
                    panelists.append(critic)
                    p_body.print("⚠️  Low consensus — escalating: added CRITIC")

            # ── Parallel Branching Sub-Sessions (Upgrade 17) ──────────────────
            if round_num == 2 and consensus_score < 0.35 and agent_count >= 4:
                try:
                    approach_forks = self._detect_approach_forks(round_entries)
                    if len(approach_forks) >= 2:
                        p_body.print(f"🔀 Detected {len(approach_forks)} divergent approaches — spawning branches")
                        self._append_war_section(f"\n🔀 Spawning {min(len(approach_forks), 3)} branch analyses...\n")

                        branch_tasks = [
                            self._run_mini_branch(
                                problem=f"Evaluate ONLY this approach: {approach}\n\nOriginal: {problem[:500]}",
                                agent_count=2,
                            )
                            for approach in approach_forks[:3]  # max 3 branches
                        ]
                        branch_results = await asyncio.gather(*branch_tasks, return_exceptions=True)

                        branch_entries = []
                        for i, result in enumerate(branch_results):
                            if isinstance(result, dict):
                                branch_entries.append({
                                    "agent": f"BRANCH_{i+1}",
                                    "role": f"Branch: {approach_forks[i][:40]}",
                                    "round": "branch",
                                    "raw": str(result),
                                    "structured": result,
                                })

                        if branch_entries:
                            blackboard.append({"round": "branch_analysis", "entries": branch_entries})
                            idea_registry.add_branch_approaches(approach_forks)
                            self._append_war_section(f"\n🔀 {len(branch_entries)} branch analyses completed\n")
                except Exception as exc:
                    p_body.print(f"⚠️ Branch analysis failed: {exc}")

            # ── Intermediate Checkpoint Synthesis (Upgrade 18) ────────────────
            if round_num % 5 == 0 and round_num > 0:
                try:
                    checkpoint = await self._run_checkpoint_synthesis(
                        problem=problem,
                        blackboard=blackboard,
                        idea_registry=idea_registry,
                        round_num=round_num,
                    )
                    self._append_war_section(
                        f"\n📍 CHECKPOINT R{round_num}: "
                        f"{checkpoint.get('consensus_action', '...')[:200]}\n"
                    )
                    self._save_checkpoint_to_memory(checkpoint, round_num)
                except Exception as exc:
                    p_body.print(f"⚠️ Checkpoint synthesis failed: {exc}")

            # ── Speculative Background Synthesis (Upgrade 10) ─────────────────
            if round_num == 1 and effective_ceiling > 1 and speculative_synth_task is None:
                speculative_synth_task = asyncio.create_task(
                    self._run_synthesizer(
                        problem=problem,
                        blackboard=blackboard,
                        consensus_score=consensus_score,
                        env_hints=env_hints,
                        idea_registry=idea_registry,
                    )
                )

        # ── Dead End Protocol ─────────────────────────────────────────────────
        if stale_streak >= DEAD_END_THRESHOLD:
            p_body.print("🔍 Dead end detected — confirming with LLM...")
            self._append_war_section("\n🔍 Dead end detected — confirming...\n")
            confirmed = await self._confirm_dead_end(idea_registry, problem)
            if not confirmed:
                # LLM found a new angle — run one more round
                p_body.print("🔄 New angle discovered — running additional round")
                self._append_war_section("🔄 New angle discovered — extra round\n")
                stale_streak = 0
                round_num += 1
                round_entries = await self._run_parallel_round(
                    panelists=panelists,
                    problem=problem,
                    blackboard=blackboard,
                    round_num=round_num,
                    env_hints=env_hints,
                    p_body=p_body,
                    idea_registry=idea_registry,
                )
                blackboard.append({"round": round_num, "entries": round_entries})
                idea_registry.register_and_count_novel(round_entries)
                consensus_score = self._compute_consensus(round_entries)
            else:
                dead_end_info = {
                    "total_rounds": round_num,
                    "total_unique_ideas": len(idea_registry.ideas),
                    "blocked_approaches": dict(idea_registry.blocked_angles),
                    "explored_angles": list(idea_registry.explored_angles),
                    "reason": "Novelty exhausted — LLM confirmed no unexplored angles remain",
                }
                p_body.print(
                    f"🛑 Dead end confirmed after {round_num} rounds, "
                    f"{len(idea_registry.ideas)} unique ideas"
                )
                self._append_war_section(
                    f"\n## EXPLORATION COMPLETE — Dead End Reached\n"
                    f"- Total rounds: {round_num}\n"
                    f"- Total unique ideas explored: {len(idea_registry.ideas)}\n"
                    f"- Blocked approaches: {list(idea_registry.blocked_angles.items())}\n"
                    f"- Explored angles: {idea_registry.explored_angles}\n"
                    f"- Reason: Novelty exhausted — confirmed by LLM\n",
                )

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

        # ── Check speculative synthesis (Upgrade 10) ──────────────────────────
        use_speculative = False
        synthesis_raw = ""
        synthesis = {}
        if speculative_synth_task is not None:
            try:
                if speculative_synth_task.done():
                    if round_num <= 2 and len(idea_registry._round_novel_counts) > 1:
                        round2_novelty = idea_registry._round_novel_counts[1]
                        if round2_novelty == 0:
                            speculative_raw = speculative_synth_task.result()
                            speculative = self._safe_json(speculative_raw)
                            if speculative.get("for_agent_zero"):
                                synthesis_raw = speculative_raw
                                synthesis = speculative
                                use_speculative = True
                                self._append_war_section("\n⚡ Used speculative synthesis (round 2 was stale)\n")
                if not use_speculative:
                    speculative_synth_task.cancel()
            except Exception:
                pass

        # ── SYNTHESIZER — always called unless speculative reused ──────────────
        if not use_speculative:
            await self.set_progress("🔬 SYNTHESIZER building final consensus...")
            p_div.print(f"\n{'─'*55}\n  SYNTHESIZER (Final Judge)\n{'─'*55}")

            synthesis_raw = await self._run_synthesizer(
                problem=problem,
                blackboard=blackboard,
                consensus_score=consensus_score,
                env_hints=env_hints,
                idea_registry=idea_registry,
            )
            synthesis = self._safe_json(synthesis_raw)
        else:
            p_div.print(f"\n{'─'*55}\n  SYNTHESIZER (Speculative — reused)\n{'─'*55}")

        # ── Populate Todo List from War Room synthesis ────────────────────────
        # Only populate todos for planning sessions (first call or explicit planning).
        # Auto-triggered (fast mode from errors) and tactical calls skip this.
        _skip_todo = (
            mode_override == "fast"  # Auto-triggered error analysis
            or self.args.get("_skip_todo_populate", False)  # Explicit flag
        )
        if not _skip_todo:
            await self._populate_todo(synthesis, idea_registry)

        # ── Cross-Session Memory Save (Upgrade 12) ────────────────────────────
        await self._save_to_memory(problem, synthesis, idea_registry)

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
            dead_end_info=dead_end_info,
            idea_registry=idea_registry,
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
            dead_end_info=dead_end_info,
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

    # ── Tool Delegation ───────────────────────────────────────────────────

    async def _delegate_tool_call(self, tool_name: str, tool_args: dict) -> str:
        """Invoke a tool internally during War Room deliberation.

        Only search_engine, document_query, and todo are allowed.
        Returns the tool's response message as a string.
        """
        allowed = {"search_engine", "document_query", "todo"}
        if tool_name not in allowed:
            return f"[delegate error: '{tool_name}' not in {allowed}]"

        try:
            if tool_name == "search_engine":
                from tools.search_engine import SearchEngine
                tool_cls = SearchEngine
            elif tool_name == "document_query":
                from tools.document_query import DocumentQueryTool
                tool_cls = DocumentQueryTool
            elif tool_name == "todo":
                from tools.todo import Todo
                tool_cls = Todo
            else:
                return f"[delegate error: unknown tool '{tool_name}']"

            method = tool_args.pop("method", None)
            tool_instance = tool_cls(
                agent=self.agent,
                name=tool_name,
                method=method,
                args=dict(tool_args),
                message="",
                loop_data=self.loop_data,
            )
            result = await tool_instance.execute(**tool_args)
            return result.message if result else ""
        except Exception as exc:
            return f"[delegate error ({tool_name}): {exc}]"

    async def _research_enrich(self, problem: str) -> list[dict]:
        """Run search_engine to gather research context before deliberation begins."""
        # Ask the LLM to extract 2-3 key search queries from the problem
        extract_msgs = [
            SystemMessage(content=(
                "You extract concise search queries from a problem statement.\n"
                "Output ONLY a JSON array of 2-3 short search query strings.\n"
                "Example: [\"python asyncio deadlock debugging\", \"asyncio gather exception handling\"]\n"
                "No prose, no markdown fences."
            )),
            HumanMessage(content=f"Problem:\n{problem[:2000]}"),
        ]
        raw = await self._llm_call(extract_msgs, temperature=0.0)
        queries = self._safe_json(raw)
        if isinstance(queries, dict):
            queries = queries.get("queries", [])
        if not isinstance(queries, list):
            return []

        entries: list[dict] = []
        for q in queries[:3]:
            if not isinstance(q, str) or not q.strip():
                continue
            try:
                result_text = await self._delegate_tool_call("search_engine", {"query": q.strip()})
                if result_text and not result_text.startswith("[delegate error"):
                    entries.append({
                        "agent": "RESEARCHER",
                        "role": f"Search: {q[:60]}",
                        "round": "research",
                        "raw": result_text[:3000],
                        "structured": {
                            "position": f"Search results for: {q}",
                            "suggested_action": "",
                            "key_risk": "Search results may be incomplete or outdated",
                            "confidence": 0.4,
                        },
                    })
            except Exception:
                pass  # never crash the War Room over a failed search
        return entries

    # Router result cache: keyed by MD5 of first 200 chars of problem text
    _router_cache: OrderedDict[str, dict] = OrderedDict()

    @staticmethod
    def _fast_route(problem: str, mode_override: str) -> dict | None:
        """Heuristic fast-path routing for obvious cases. Returns route dict or None."""
        if mode_override:
            return None  # explicit override takes its own path

        text = problem.strip()
        for pat in _TRIVIAL_PATTERNS:
            if pat.search(text):
                return {"complexity": "TRIVIAL", "agent_count": 1, "rounds": 1, "mode": "execution"}
        for pat in _CRITICAL_PATTERNS:
            if pat.search(text):
                return {"complexity": "CRITICAL", "agent_count": 6, "rounds": 3, "mode": "planning"}
        for pat in _HIGH_PATTERNS:
            if pat.search(text):
                return {"complexity": "HIGH", "agent_count": 4, "rounds": 2, "mode": "planning"}
        return None

    @staticmethod
    def _router_cache_key(problem: str) -> str:
        """MD5 hash of first 200 chars for cache keying."""
        return hashlib.md5(problem[:200].encode("utf-8", errors="replace")).hexdigest()

    async def _run_router(self, problem: str, mode_override: str) -> dict:
        """Run the complexity router; returns route dict with fallback defaults."""
        if mode_override in ("planning", "analysis", "execution"):
            mapping = {
                "planning":  {"complexity": "HIGH",   "agent_count": 5, "rounds": 3, "mode": "planning"},
                "analysis":  {"complexity": "MEDIUM", "agent_count": 3, "rounds": 2, "mode": "analysis"},
                "execution": {"complexity": "LOW",    "agent_count": 2, "rounds": 1, "mode": "execution"},
            }
            return mapping[mode_override]

        # Check router cache
        cache_key = self._router_cache_key(problem)
        if cache_key in Think._router_cache:
            Think._router_cache.move_to_end(cache_key)
            return Think._router_cache[cache_key]

        # Fast-path heuristic routing
        fast = self._fast_route(problem, mode_override)
        if fast is not None:
            Think._router_cache[cache_key] = fast
            if len(Think._router_cache) > _ROUTER_CACHE_MAX:
                Think._router_cache.popitem(last=False)
            return fast

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

        # Cache the LLM result
        Think._router_cache[cache_key] = result
        if len(Think._router_cache) > _ROUTER_CACHE_MAX:
            Think._router_cache.popitem(last=False)
        return result

    async def _run_one_panelist(
        self,
        panelist: dict,
        problem: str,
        blackboard: list[dict],
        round_num: int,
        env_hints: list[str],
        idea_registry: Optional[IdeaRegistry] = None,
        contrarian_prefix: str = "",
        lateral_prompt: str = "",
    ) -> dict:
        """Single panelist call; returns entry dict for the blackboard."""
        # Build blackboard snapshot for this panelist
        board_text = self._render_blackboard(blackboard, idea_registry=idea_registry)
        env_section = (
            "\n\nENVIRONMENT ISSUES DETECTED (consider in your response):\n"
            + "\n".join(f"- {h}" for h in env_hints)
        ) if env_hints else ""

        # Inject idea registry state so panelists avoid repeating explored ideas
        idea_section = ""
        if idea_registry and idea_registry.explored_angles:
            explored = ", ".join(idea_registry.explored_angles[:20])
            pending = ", ".join(idea_registry.pending_angles[:10]) or "(none)"
            blocked = ", ".join(f"{k}: {v}" for k, v in list(idea_registry.blocked_angles.items())[:10]) or "(none)"
            idea_section = (
                "\n\nIDEA REGISTRY STATE (do NOT repeat these):\n"
                f"- Explored: [{explored}]\n"
                f"- Pending/Unexplored: [{pending}]\n"
                f"- Blocked: [{blocked}]\n"
                "Your job: contribute ideas NOT on the Explored list.\n"
                "If you cannot find a new angle, say explicitly: "
                "\"I have no new approaches to add.\""
            )

        # Phase-based round architecture (Upgrade 7) + Adaptive temperature (Upgrade 9)
        phase = _get_phase(round_num)
        phase_prompt = _PHASE_PROMPTS[phase].format(n=round_num)
        temp = _get_temperature(phase, panelist["name"])

        human_content = (
            f"TASK:\n{problem}"
            f"{env_section}"
            f"{idea_section}"
            + (
                f"\n\nBLACKBOARD (all prior rounds):\n{board_text}"
                if board_text else
                "\n\n(You are first to speak — no prior discussion.)"
            )
            + f"\n\n{phase_prompt}"
        )

        if contrarian_prefix:
            human_content = contrarian_prefix + human_content

        if lateral_prompt:
            human_content = lateral_prompt + "\n\n" + human_content

        msgs = [
            SystemMessage(content=panelist["system"]),
            HumanMessage(content=human_content),
        ]
        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    msgs,
                    temperature=temp,
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
        idea_registry: Optional[IdeaRegistry] = None,
        lateral_prompt: str = "",
    ) -> list[dict]:
        """Fire all panelists in parallel; stream each result to WebUI as it arrives."""
        await self.set_progress(
            f"🧠 Round {round_num} — {len(panelists)} panelists firing in parallel..."
        )

        # Tagged wrapper bundles panelist identity with its result so we can
        # stream results to the WebUI in completion order (as_completed) without
        # a dict lookup (which fails because as_completed wraps futures).
        async def _tagged(p: dict, c_prefix: str = "", l_prompt: str = "") -> tuple[dict, dict]:
            try:
                entry = await self._run_one_panelist(
                    p, problem, blackboard, round_num, env_hints, idea_registry,
                    contrarian_prefix=c_prefix,
                    lateral_prompt=l_prompt,
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

        # ── Contrarian rotation (Upgrade 8) ──────────────────────────────────
        contrarian_idx = -1
        contrarian_prefix_str = ""
        if round_num >= 2 and idea_registry:
            dominant = idea_registry.get_most_endorsed_action()
            if dominant:
                contrarian_idx = round_num % len(panelists)
                contrarian_prefix_str = (
                    f"MANDATORY CONTRARIAN ROLE FOR THIS ROUND:\n"
                    f"The dominant emerging position is: '{dominant[:200]}'\n"
                    f"Your ONLY job this round is to find reasons this is WRONG or INCOMPLETE.\n"
                    f"Do NOT agree with this position. Find the attack on it.\n"
                    f"If you genuinely cannot find a flaw, say exactly: "
                    f"'No valid objection found — approach is sound because [specific reason]'\n\n"
                )

        tasks = [
            _tagged(p, contrarian_prefix_str if i == contrarian_idx else "", lateral_prompt)
            for i, p in enumerate(panelists)
        ]
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

    @staticmethod
    def _validate_synthesis(synthesis: dict) -> list[str]:
        """Validate synthesizer JSON output. Returns list of error strings (empty = valid)."""
        errors: list[str] = []

        fa = synthesis.get("for_agent_zero")
        if not isinstance(fa, dict):
            errors.append("Missing or invalid 'for_agent_zero' block (must be a dict).")
            return errors  # can't validate further without this block

        tool_name = fa.get("tool_name")
        if tool_name not in _VALID_TOOL_NAMES:
            errors.append(
                f"Invalid tool_name '{tool_name}'. "
                f"Must be one of: {', '.join(sorted(_VALID_TOOL_NAMES))}"
            )

        tool_args = fa.get("tool_args")
        if not isinstance(tool_args, dict):
            errors.append("'tool_args' must be a dict.")
        elif tool_name in _TOOL_REQUIRED_ARGS:
            missing = [k for k in _TOOL_REQUIRED_ARGS[tool_name] if k not in tool_args]
            if missing:
                errors.append(
                    f"tool_args for '{tool_name}' missing required keys: {', '.join(missing)}"
                )

        thoughts = fa.get("thoughts")
        if not thoughts:
            errors.append("'thoughts' should not be empty.")

        headline = fa.get("headline")
        if not headline:
            errors.append("'headline' should not be empty.")

        return errors

    async def _run_synthesizer(
        self,
        problem: str,
        blackboard: list[dict],
        consensus_score: float,
        env_hints: list[str],
        idea_registry: Optional[IdeaRegistry] = None,
    ) -> str:
        """Always-called synthesizer; produces strict JSON with auto-retry on validation failure."""
        board_text = self._render_blackboard(blackboard, idea_registry=idea_registry)
        env_section = (
            "\n\nENVIRONMENT ISSUES TO ADDRESS IN REPAIR STEP:\n"
            + "\n".join(f"- {h}" for h in env_hints)
        ) if env_hints else ""

        # ── Idea Momentum Scoring (Upgrade 13) ────────────────────────────────
        momentum_section = ""
        if idea_registry:
            top_ideas = idea_registry.get_top_ideas(n=5)
            if top_ideas:
                momentum_lines: list[str] = []
                for i, idea in enumerate(top_ideas):
                    s = idea.get("structured", {})
                    fp_key = idea_registry._fingerprint(
                        f"{s.get('suggested_action', '')} {s.get('position', '')}"
                    )
                    refs = idea_registry.idea_endorsements.get(fp_key, 1)
                    momentum_lines.append(
                        f"{i+1}. [{idea.get('agent', '?')} × {refs} refs] "
                        f"{s.get('suggested_action', '?')[:200]}"
                    )
                momentum_section = (
                    "\n\nHIGH-MOMENTUM IDEAS (referenced most by panelists — "
                    "give these extra weight):\n"
                    + "\n".join(momentum_lines)
                )

        human = (
            f"ORIGINAL TASK:\n{problem}"
            f"{env_section}"
            f"{momentum_section}"
            f"\n\nCONSENSUS SCORE AFTER DEBATE: {consensus_score:.2f}"
            f"\n\nFULL BLACKBOARD:\n{board_text}"
            "\n\nProduce the final JSON consensus decision now."
        )

        msgs = [
            SystemMessage(content=_SYNTHESIZER["system"]),
            HumanMessage(content=human),
        ]

        max_attempts = 3  # 1 initial + 2 retries
        best_raw = ""
        for attempt in range(max_attempts):
            raw = await self._llm_call(msgs, temperature=_get_temperature("synthesis", "SYNTHESIZER"))
            best_raw = raw  # always keep latest as best-effort

            synthesis = self._safe_json(raw)
            validation_errors = self._validate_synthesis(synthesis)

            if not validation_errors:
                break  # valid output

            if attempt < max_attempts - 1:
                # Append correction message and retry
                correction = (
                    "Your previous output had validation errors:\n"
                    + "\n".join(f"- {e}" for e in validation_errors)
                    + "\n\nPlease fix these issues and output the corrected JSON only."
                )
                msgs.append(HumanMessage(content=raw))
                msgs.append(HumanMessage(content=correction))
            else:
                # Max retries exhausted — log warning and use best effort
                PrintStyle(font_color="orange", padding=True).print(
                    f"Synthesizer validation failed after {max_attempts} attempts: "
                    + "; ".join(validation_errors)
                )

        # Stream synthesizer output into consolidated war log
        synthesis = self._safe_json(best_raw)
        self._clear_war_live_preview()
        self._append_war_section(
            (
                f"\n━━ SYNTHESIS ━━━━━━━━━━━━━━━\n"
                f"Consensus: \"{synthesis.get('consensus_action', best_raw[:200])}\"\n"
                f"Confidence: {synthesis.get('confidence', '?')}\n"
            ),
        )
        return best_raw

    async def _confirm_dead_end(self, idea_registry: IdeaRegistry, problem: str) -> bool:
        """Ask LLM if there are truly no unexplored angles. Returns True if dead end confirmed."""
        summary = idea_registry.get_summary()
        pending = idea_registry.get_unexplored_angles()

        msgs = [
            SystemMessage(content="You are an exploration completeness checker."),
            HumanMessage(content=(
                f"IDEA REGISTRY:\n{summary}\n\n"
                f"PENDING ANGLES: {pending}\n\n"
                f"ORIGINAL PROBLEM: {problem[:500]}\n\n"
                "Question: Are there ANY unexplored approaches, tools, techniques, "
                "pathways, edge cases, or sub-problems we have NOT addressed?\n"
                "Answer ONLY: YES [specific angle] or NO [brief reason why exhausted]"
            )),
        ]
        raw = await self._llm_call(msgs, temperature=0.1, panelist_name="DEAD_END_CHECK")

        if raw.strip().upper().startswith("YES"):
            # Extract the angle
            angle = raw.strip()[3:].strip().lstrip(":-–— ").strip()
            if angle:
                idea_registry.pending_angles.append(angle[:200])
            return False  # Not a dead end — new angle found
        return True  # Dead end confirmed

    # ── utilities ─────────────────────────────────────────────────────────────

    def _render_blackboard(self, blackboard: list[dict], idea_registry: Optional[IdeaRegistry] = None) -> str:
        """Render blackboard as readable text for prompt injection."""
        # ── Blackboard Pruning for rendering (Upgrade 24) ─────────────────
        if len(blackboard) > 3 and idea_registry:
            render_bb = self._prune_blackboard(blackboard, idea_registry)
        else:
            render_bb = blackboard

        lines: list[str] = []
        for rd in render_bb:
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
        """Normalized token overlap with confidence boost. Returns 0-1.

        When entries carry idea_class (Upgrade 20), groups by class and
        computes within-class consensus for more nuanced measurement.
        """
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

        # Check if structured idea_class data is available (Upgrade 20)
        idea_classes = [
            e.get("structured", {}).get("idea_class", "unknown")
            for e in entries
        ]
        has_taxonomy = any(ic not in ("unknown", "") for ic in idea_classes)

        if has_taxonomy:
            # Group by idea_class and compute within-class consensus
            class_groups: dict[str, list[int]] = {}
            for i, ic in enumerate(idea_classes):
                class_groups.setdefault(ic, []).append(i)

            class_scores: list[float] = []
            class_sizes: list[int] = []
            for ic, indices in class_groups.items():
                class_sizes.append(len(indices))
                if len(indices) < 2:
                    class_scores.append(1.0)  # single entry = self-consensus
                    continue
                pairs: list[float] = []
                for ii in range(len(indices)):
                    for jj in range(ii + 1, len(indices)):
                        a, b = sets[indices[ii]], sets[indices[jj]]
                        if not a and not b:
                            pairs.append(1.0)
                        elif not a or not b:
                            pairs.append(0.0)
                        else:
                            pairs.append(len(a & b) / min(len(a), len(b)))
                class_scores.append(sum(pairs) / len(pairs) if pairs else 1.0)

            # Weight by class size
            total_entries = len(entries)
            base_score = sum(
                score * size / total_entries
                for score, size in zip(class_scores, class_sizes)
            )

            # Penalize high class fragmentation (many classes = less consensus)
            n_classes = len(class_groups)
            if n_classes > 1:
                fragmentation_penalty = 0.1 * (n_classes - 1) / max(total_entries - 1, 1)
                base_score = max(base_score - fragmentation_penalty, 0.0)
        else:
            # Original pairwise overlap logic
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
        dead_end_info: dict | None = None,
    ) -> str:
        """Build a compact result for the agent's message history (~200-400 tokens).

        The FOR_AGENT_ZERO directive is placed FIRST so the main agent sees the
        mandatory action before generating any response tokens.
        """
        faz = synthesis.get("for_agent_zero", {})
        confidence = synthesis.get("confidence", "?")
        consensus = synthesis.get("consensus_action", "See synthesis below")
        reasoning = synthesis.get("reasoning_trace", synthesis_raw)

        risks = synthesis.get("key_risks", [])[:2]
        dissent = synthesis.get("dissent_notes", [])[:1]

        lines: list[str] = []

        if faz:
            lines += [
                "## ⚡ MANDATORY NEXT ACTION — Execute this tool call immediately:",
                "```json",
                json.dumps(faz, indent=2),
                "```",
                "Do not elaborate first. Execute this call as your next response.",
                "",
            ]

        lines.append(
            f"**War Room Summary:** {len(panelists)} panelists, {round_count} rounds, Conf: {confidence} ({elapsed:.1f}s)"
        )
        lines.append(f"**Consensus:** {consensus}")

        if risks:
            lines.append("**Key Risks:** " + "; ".join(risks))
        if dissent:
            lines.append("**Dissent:** " + dissent[0])

        trace = str(reasoning)[:200]
        if len(str(reasoning)) > 200:
            trace += "…"
        lines.append(f"**Reasoning:** {trace}")

        if dead_end_info:
            lines += [
                "",
                f"**Exploration Complete:** {dead_end_info.get('total_unique_ideas', '?')} unique ideas "
                f"across {dead_end_info.get('total_rounds', '?')} rounds",
                f"**Termination:** {dead_end_info.get('reason', 'dead end')}",
            ]

        if not faz:
            lines += ["", "```", synthesis_raw[:600], "```"]

        return "\n".join(lines)

    def _format_verbose_log(
        self,
        blackboard: list[dict],
        synthesis: dict,
        synthesis_raw: str,
        elapsed: float,
        panelists: list[dict],
        round_count: int,
        dead_end_info: dict | None = None,
        idea_registry: Optional[IdeaRegistry] = None,
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

        if dead_end_info:
            lines += [
                "",
                "## 🛑 EXPLORATION COMPLETE — Dead End Reached",
                f"- Total rounds: {dead_end_info.get('total_rounds', '?')}",
                f"- Total unique ideas explored: {dead_end_info.get('total_unique_ideas', '?')}",
                f"- Blocked approaches: {dead_end_info.get('blocked_approaches', {})}",
                f"- Explored angles: {dead_end_info.get('explored_angles', [])}",
                f"- Reason: {dead_end_info.get('reason', 'dead end')}",
            ]

        risks = synthesis.get("key_risks", [])
        if risks:
            lines += ["", "**Key Risks:**"] + [f"- {r}" for r in risks]

        dissent = synthesis.get("dissent_notes", [])
        if dissent:
            lines += ["", "**Dissent Notes:**"] + [f"- {d}" for d in dissent]

        # ── High-Momentum Ideas (Upgrade 13) ──────────────────────────────────
        if idea_registry:
            top_ideas = idea_registry.get_top_ideas(n=5)
            if top_ideas:
                lines += ["", "---", "## 🚀 High-Momentum Ideas"]
                for i, idea in enumerate(top_ideas):
                    s = idea.get("structured", {})
                    fp_key = idea_registry._fingerprint(
                        f"{s.get('suggested_action', '')} {s.get('position', '')}"
                    )
                    refs = idea_registry.idea_endorsements.get(fp_key, 1)
                    lines.append(
                        f"{i+1}. **[{idea.get('agent', '?')} × {refs} refs]** "
                        f"{s.get('suggested_action', '?')[:200]}"
                    )

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

    # ─────────────────────────────────────────────────────────────────────────
    #  LATERAL THINKING INJECTOR (Upgrade 4)
    # ─────────────────────────────────────────────────────────────────────────
    def _build_lateral_injection(
        self, stale_streak: int, used_laterals: set[str]
    ) -> str | None:
        """Cycle through lateral seeds, skipping used ones. Returns prompt or None."""
        for name, prompt in _LATERAL_SEEDS:
            if name not in used_laterals:
                used_laterals.add(name)
                return (
                    f"🔀 LATERAL THINKING INJECTION — {name} MODE:\n"
                    f"{prompt}\n"
                    f"Apply this frame to the current problem. "
                    f"Break out of the current line of thinking.\n"
                )
        return None  # all laterals exhausted

    # ─────────────────────────────────────────────────────────────────────────
    #  PARALLEL BRANCHING SUB-SESSIONS (Upgrade 17)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _detect_approach_forks(entries: list[dict]) -> list[str]:
        """
        Group entries by semantic similarity of suggested_action.
        Uses keyword overlap: if < 0.30 overlap with ALL existing cluster
        centroids, the entry starts a new cluster.
        Returns list of approach descriptions (one per distinct cluster).
        """
        if not entries:
            return []

        def _extract_kw(text: str) -> set[str]:
            words = re.findall(r"[a-zA-Z]{4,}", text.lower())
            return {w for w in words if w not in _STOP_WORDS}

        # Each cluster: {"keywords": set, "description": str, "entries": list}
        clusters: list[dict] = []

        for entry in entries:
            s = entry.get("structured", {})
            action = s.get("suggested_action", "")
            if not action or action in ("timeout", "error"):
                continue
            kw = _extract_kw(action)
            if not kw:
                continue

            matched_cluster = None
            best_overlap = 0.0

            for cluster in clusters:
                centroid_kw = cluster["keywords"]
                if not centroid_kw:
                    continue
                overlap = len(kw & centroid_kw) / max(min(len(kw), len(centroid_kw)), 1)
                if overlap > best_overlap:
                    best_overlap = overlap
                    matched_cluster = cluster

            if best_overlap < 0.30 or matched_cluster is None:
                # New cluster
                clusters.append({
                    "keywords": set(kw),
                    "description": action[:120],
                    "entries": [entry],
                })
            else:
                # Add to best-matching cluster; update centroid keywords (union)
                matched_cluster["keywords"] |= kw
                matched_cluster["entries"].append(entry)

        return [c["description"] for c in clusters]

    async def _run_mini_branch(
        self,
        problem: str,
        agent_count: int = 2,
    ) -> dict:
        """
        Run a mini war room session: 2 panelists (STRATEGIST + EXECUTOR),
        1 round, no IdeaRegistry. Returns a synthetic entry dict with the
        branch's conclusion.
        """
        mini_panelists = [
            p for p in _ALL_PANELISTS if p["name"] in ("STRATEGIST", "EXECUTOR")
        ][:agent_count]

        tasks = []
        for p in mini_panelists:
            msgs = [
                SystemMessage(content=p["system"]),
                HumanMessage(content=f"TASK:\n{problem}\n\n(Mini-branch analysis — be concise and decisive.)"),
            ]
            tasks.append(self._llm_call(msgs, temperature=0.25, panelist_name=f"BRANCH_{p['name']}"))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Merge mini-branch results into a single synthetic entry
        positions = []
        actions = []
        risks = []
        for raw in results:
            if isinstance(raw, str):
                parsed = self._safe_json(raw)
                positions.append(parsed.get("position", raw[:80]))
                actions.append(parsed.get("suggested_action", ""))
                risks.append(parsed.get("key_risk", ""))

        return {
            "position": " | ".join(p for p in positions if p)[:200] or "Branch inconclusive",
            "suggested_action": " → ".join(a for a in actions if a)[:200] or "No clear action",
            "key_risk": "; ".join(r for r in risks if r)[:150] or "Unknown",
            "confidence": 0.5,
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  BLACKBOARD PRUNING + QUALITY FILTERING (Upgrade 24)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _prune_blackboard(
        blackboard: list[dict],
        idea_registry: IdeaRegistry,
        max_entries_per_round: int = 4,
    ) -> list[dict]:
        """
        Return a pruned copy of the blackboard for rendering purposes only.
        Keeps top entries per round by score (endorsement × 2 + confidence).
        Special rounds (flash, historical, branch_analysis, decomposition) are
        always kept intact.
        """
        _KEEP_ROUNDS = {"flash", "historical", "branch_analysis", "decomposition"}
        pruned: list[dict] = []

        for rd in blackboard:
            r = rd["round"]
            entries = rd.get("entries", [])

            # Always keep special rounds intact
            if r in _KEEP_ROUNDS or len(entries) <= max_entries_per_round:
                pruned.append(rd)
                continue

            # Score each entry: endorsement_count * 2 + confidence
            scored: list[tuple[float, dict]] = []
            for entry in entries:
                s = entry.get("structured", {})
                text = f"{s.get('suggested_action', '')} {s.get('position', '')}"
                fp = idea_registry._fingerprint(text, structured=s)
                endorsements = idea_registry.idea_endorsements.get(fp, 1)
                confidence = float(s.get("confidence", 0.5))
                score = endorsements * 2 + confidence
                scored.append((score, entry))

            # Sort descending by score, keep top N
            scored.sort(key=lambda x: x[0], reverse=True)
            kept = [entry for _, entry in scored[:max_entries_per_round]]
            pruned.append({"round": r, "entries": kept})

        return pruned

    # ─────────────────────────────────────────────────────────────────────────
    #  PROBLEM DECOMPOSITION PRE-PASS (Upgrade 22)
    # ─────────────────────────────────────────────────────────────────────────
    async def _run_decomposition(self, problem: str) -> list[str]:
        """
        Use LLM to break a CRITICAL problem into 3-6 sub-problems.
        Returns list of sub-problem description strings.
        """
        msgs = [
            SystemMessage(content=(
                "You are a problem decomposition specialist.\n"
                "Break the given problem into 3-6 independent sub-problems.\n"
                "Output ONLY a JSON array of strings — no prose, no markdown fences.\n"
                'Example: ["sub-problem 1 description", "sub-problem 2 description", ...]\n'
                "Each sub-problem should be self-contained and actionable.\n"
                "Focus on orthogonal concerns: don't repeat the same idea.\n"
                "Return between 3 and 6 sub-problems."
            )),
            HumanMessage(content=f"PROBLEM TO DECOMPOSE:\n{problem[:2000]}"),
        ]
        raw = await self._llm_call(msgs, temperature=0.2, panelist_name="DECOMPOSER")

        # Parse JSON array
        try:
            cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
            start = cleaned.find("[")
            end = cleaned.rfind("]")
            if start >= 0 and end >= 0:
                result = json.loads(cleaned[start : end + 1])
                if isinstance(result, list):
                    return [str(s) for s in result if s][:6]
        except Exception:
            pass

        # Fallback: try line-by-line parsing
        lines = [l.strip().lstrip("-•0123456789.)").strip() for l in raw.strip().split("\n")]
        return [l for l in lines if len(l) > 10][:6]

    async def _run_mini_warroom(
        self,
        sub_problem: str,
        agent_count: int = 2,
        rounds: int = 1,
    ) -> dict:
        """
        Run a minimal war room: 2 panelists (STRATEGIST + EXECUTOR), 1 round.
        Returns a result dict suitable for blackboard entry 'structured' field.
        """
        mini_panelists = [
            p for p in _ALL_PANELISTS if p["name"] in ("STRATEGIST", "EXECUTOR")
        ][:agent_count]

        tasks = []
        for p in mini_panelists:
            msgs = [
                SystemMessage(content=p["system"]),
                HumanMessage(
                    content=(
                        f"TASK:\n{sub_problem[:1000]}\n\n"
                        "(Mini war room — sub-problem analysis. Be concise and decisive.)"
                    )
                ),
            ]
            tasks.append(self._llm_call(msgs, temperature=0.25, panelist_name=f"DECOMP_{p['name']}"))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Merge results into a synthetic entry
        positions = []
        actions = []
        risks = []
        for raw in results:
            if isinstance(raw, str):
                parsed = self._safe_json(raw)
                positions.append(parsed.get("position", raw[:80]))
                actions.append(parsed.get("suggested_action", ""))
                risks.append(parsed.get("key_risk", ""))

        return {
            "position": " | ".join(p for p in positions if p)[:200] or "Sub-problem inconclusive",
            "suggested_action": " → ".join(a for a in actions if a)[:200] or "No clear action",
            "key_risk": "; ".join(r for r in risks if r)[:150] or "Unknown",
            "confidence": 0.5,
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  INTERMEDIATE CHECKPOINT SYNTHESIS (Upgrade 18)
    # ─────────────────────────────────────────────────────────────────────────
    async def _run_checkpoint_synthesis(
        self,
        problem: str,
        blackboard: list[dict],
        idea_registry: IdeaRegistry,
        round_num: int,
    ) -> dict:
        """
        Lightweight mid-session synthesis using idea_registry summary
        instead of full blackboard render. For progress visibility and
        crash recovery.
        """
        registry_summary = idea_registry.get_summary()

        msgs = [
            SystemMessage(content=(
                "You are a checkpoint summarizer for an ongoing war room debate.\n"
                "Produce a checkpoint summary of the current best approach.\n"
                "Output ONLY this JSON (no prose, no markdown fences):\n"
                "{\n"
                '  "consensus_action": "current best approach in one sentence",\n'
                '  "confidence": 0.5,\n'
                '  "key_risks": ["risk 1"],\n'
                '  "explored_count": 0,\n'
                '  "round": 0\n'
                "}"
            )),
            HumanMessage(content=(
                f"ORIGINAL TASK:\n{problem[:500]}\n\n"
                f"ROUND: {round_num}\n\n"
                f"IDEA REGISTRY STATE:\n{registry_summary}\n\n"
                "Produce the checkpoint summary JSON now."
            )),
        ]

        raw = await self._llm_call(msgs, temperature=0.10, panelist_name="CHECKPOINT")
        result = self._safe_json(raw)
        result.setdefault("round", round_num)
        result.setdefault("explored_count", len(idea_registry.ideas))
        return result

    def _save_checkpoint_to_memory(self, checkpoint: dict, round_num: int) -> None:
        """
        Save checkpoint to agent.data for crash recovery and progress tracking.
        Uses the same in-memory mechanism as cross-session memory (Upgrade 12).
        """
        try:
            checkpoints = self.agent.data.setdefault("_warroom_checkpoints", [])
            checkpoints.append({
                "round": round_num,
                "timestamp": time.time(),
                "consensus_action": checkpoint.get("consensus_action", "")[:300],
                "confidence": checkpoint.get("confidence", 0),
                "explored_count": checkpoint.get("explored_count", 0),
            })
            # Cap stored checkpoints
            if len(checkpoints) > 20:
                self.agent.data["_warroom_checkpoints"] = checkpoints[-20:]

            # Also persist to file for crash recovery
            checkpoint_path = os.path.join("tmp", "warroom_checkpoint.json")
            os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                json.dump({
                    "round": round_num,
                    "timestamp": time.time(),
                    "checkpoint": checkpoint,
                }, f, default=str)
        except Exception:
            pass  # Checkpoint save is best-effort, never block the main flow

    # ─────────────────────────────────────────────────────────────────────────
    #  CROSS-SESSION MEMORY INTEGRATION (Upgrade 12)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _extract_keywords(text: str, n: int = 3) -> list[str]:
        """Extract top N high-information keywords from text."""
        words = re.findall(r"[a-zA-Z]{4,}", text.lower())
        keywords = [w for w in words if w not in _STOP_WORDS]
        seen: set[str] = set()
        unique: list[str] = []
        for w in keywords:
            if w not in seen:
                seen.add(w)
                unique.append(w)
            if len(unique) >= n:
                break
        return unique

    async def _load_historical_context(self, problem: str) -> str:
        """Load relevant past War Room syntheses from in-memory + file history."""
        results: list[str] = []
        keywords = self._extract_keywords(problem, n=5)
        keyword_set = set(keywords)

        try:
            # Check agent.data in-memory history
            history = self.agent.data.get("_warroom_history", [])
            for entry in reversed(history[-20:]):  # most recent first
                entry_kw = set(entry.get("keywords", []))
                if keyword_set & entry_kw:
                    results.append(entry.get("summary", "")[:300])
                if len(results) >= 3:
                    break
        except Exception:
            pass

        if len(results) < 3:
            try:
                # Try loading from JSONL file
                history_path = os.path.join("tmp", "warroom_history.jsonl")
                if os.path.isfile(history_path):
                    with open(history_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in reversed(lines[-50:]):
                        try:
                            entry = json.loads(line.strip())
                            entry_kw = set(entry.get("keywords", []))
                            if keyword_set & entry_kw:
                                results.append(entry.get("summary", "")[:300])
                            if len(results) >= 3:
                                break
                        except (json.JSONDecodeError, KeyError):
                            continue
            except Exception:
                pass

        if results:
            return (
                "HISTORICAL WAR ROOM CONTEXT (from past sessions on similar problems):\n"
                + "\n---\n".join(results[:3])
            )[:800]
        return ""

    async def _populate_todo(self, synthesis: dict, idea_registry: IdeaRegistry) -> None:
        """Push the War Room's action plan into the todo tool."""
        try:
            todo_plan = synthesis.get("todo_plan", [])
            if not todo_plan:
                # Fallback: create a single task from for_agent_zero
                faz = synthesis.get("for_agent_zero", {})
                if faz:
                    todo_plan = [{
                        "title": faz.get("headline", synthesis.get("consensus_action", "War Room action")),
                        "description": (faz.get("thoughts", [""])[0] if faz.get("thoughts") else ""),
                        "priority": "high" if synthesis.get("confidence", 0) >= 0.7 else "normal",
                    }]

            if not todo_plan:
                return

            # Also add high-momentum unexplored ideas as todo items
            if idea_registry:
                top_ideas = idea_registry.get_top_ideas(n=3)
                for idea in top_ideas:
                    s = idea.get("structured", {})
                    action = s.get("suggested_action", "")
                    if action and action not in [t.get("title", "") for t in todo_plan]:
                        todo_plan.append({
                            "title": action[:120],
                            "description": f"From {idea.get('agent', '?')}: {s.get('position', '')}",
                            "priority": "normal",
                        })

            # Invoke todo tool with bulk_add
            result = await self._delegate_tool_call("todo", {
                "method": "bulk_add",
                "tasks": todo_plan,
                "_caller": "think",
            })

            if result:
                self._append_war_section(f"\n📋 Todo populated: {result}\n")
        except Exception as exc:
            self._append_war_section(f"\n⚠️ Todo population failed: {exc}\n")

    async def _save_to_memory(
        self,
        problem: str,
        synthesis: dict,
        idea_registry: IdeaRegistry,
    ) -> None:
        """Save compact War Room summary to agent.data and a JSONL file."""
        try:
            keywords = self._extract_keywords(problem, n=8)
            entry = {
                "timestamp": time.time(),
                "keywords": keywords,
                "problem_summary": problem[:200],
                "consensus_action": synthesis.get("consensus_action", "")[:300],
                "confidence": synthesis.get("confidence", 0),
                "explored_angles": idea_registry.explored_angles[:10],
                "blocked_angles": dict(list(idea_registry.blocked_angles.items())[:5]),
                "summary": (
                    f"Problem: {problem[:100]}... "
                    f"Action: {synthesis.get('consensus_action', '?')[:150]} "
                    f"Conf: {synthesis.get('confidence', '?')}"
                ),
            }

            # Save to agent.data (persists within context)
            history = self.agent.data.setdefault("_warroom_history", [])
            history.append(entry)
            # Cap in-memory history
            if len(history) > 50:
                self.agent.data["_warroom_history"] = history[-50:]

            # Save to JSONL file (persists across sessions)
            history_path = os.path.join("tmp", "warroom_history.jsonl")
            os.makedirs(os.path.dirname(history_path), exist_ok=True)
            with open(history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass  # Memory save is best-effort, never block the main flow

    def get_log_object(self) -> Any:
        pre_id = str(uuid.uuid4())
        return self.agent.context.log.log(
            type="tool",
            heading=f"🏛️ {self.agent.agent_name} — War Room Thinking",
            content="Initializing War Room...",
            kvps=self.args,
            _tool_name=self.name,
            id=pre_id,
        )

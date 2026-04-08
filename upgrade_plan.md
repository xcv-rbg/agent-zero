I have all three files written and ready. Here is the **complete, copy-paste-ready implementation**. Only **3 files total**. Zero changes to `agent.py`.

***

# Agent Zero — Multi-Agent Expert Thinking (SWARM Mode)

## What You're Installing

| File | Action | Purpose |
|---|---|---|
| `tools/think.py` | **Create new** | The 4-expert LLM panel tool |
| `prompts/agent.system.tool.think.md` | **Create new** | Teaches the agent when/how to call the tool |
| `prompts/agent.system.main.solving.md` | **Replace** | Tells the agent to think before acting |

***

## File 1 — `tools/think.py` *(new file)*

```python
# tools/think.py
# Drop this file into the Agent Zero repo at: tools/think.py
# That's it. No other Python changes needed.

from helpers.tool import Tool, Response
from helpers.print_style import PrintStyle
from langchain_core.messages import SystemMessage, HumanMessage

# ─────────────────────────────────────────────────────────────────────────────
#  EXPERT DEFINITIONS  — edit roles/instructions freely for any domain
#  Default: generic 4-expert team (works for any task)
#  Swap system prompts below to specialize (see security example in comments)
# ─────────────────────────────────────────────────────────────────────────────
EXPERTS = [
    {
        "name": "ALPHA",
        "role": "Strategic Analyst",
        "color": "#aed6f1",
        "icon": "icon://psychology",
        "system": (
            "You are ALPHA, the Strategic Analyst on a 4-expert collaborative thinking panel.\n"
            "Your mission: deeply analyse the problem, identify the core challenge and hidden "
            "constraints, then propose the best high-level strategy.\n"
            "Rules:\n"
            "- Be decisive and specific. No vague language.\n"
            "- State the single most important insight first.\n"
            "- End your response with exactly: "
            "'MY RECOMMENDED APPROACH: <one concrete sentence>.'"
        ),
    },
    {
        "name": "BETA",
        "role": "Devil's Advocate",
        "color": "#f1948a",
        "icon": "icon://gavel",
        "system": (
            "You are BETA, the Devil's Advocate on a 4-expert collaborative thinking panel.\n"
            "Your mission: critically examine ALPHA's proposal. Find every flaw, risk, blind spot, "
            "and hidden assumption. Then suggest a concrete improvement or an alternative approach "
            "that fixes those weaknesses.\n"
            "Rules:\n"
            "- Reference ALPHA's specific claims when challenging them.\n"
            "- Be constructive — your goal is a better plan, not just criticism.\n"
            "- End your response with exactly: "
            "'CRITICAL FIX: <the single most important change ALPHA must make>.'"
        ),
    },
    {
        "name": "GAMMA",
        "role": "Implementer",
        "color": "#a9dfbf",
        "icon": "icon://build",
        "system": (
            "You are GAMMA, the Implementer on a 4-expert collaborative thinking panel.\n"
            "Your mission: read ALPHA and BETA carefully, then translate the best combined ideas "
            "into a concrete, numbered, step-by-step execution plan. Address BETA's critical fix "
            "in your steps.\n"
            "Rules:\n"
            "- Write steps an agent can execute directly with real tools.\n"
            "- Include specific tool names, file paths, commands, or queries where relevant.\n"
            "- End your response with exactly: "
            "'EDGE CASES: <top 2 edge cases to watch out for>.'"
        ),
    },
    {
        "name": "DELTA",
        "role": "Synthesizer & Judge",
        "color": "#f9e79f",
        "icon": "icon://balance",
        "system": (
            "You are DELTA, the Synthesizer and final Judge on a 4-expert collaborative thinking panel.\n"
            "Your mission: read ALL three experts above. Produce the final consensus that the main "
            "agent will use as its execution blueprint. Resolve any disagreements.\n"
            "You MUST use EXACTLY this output format — do not deviate:\n\n"
            "CONSENSUS PLAN:\n"
            "1. [step]\n"
            "2. [step]\n"
            "(as many steps as needed)\n\n"
            "KEY RISKS:\n"
            "- [risk]\n"
            "- [risk]\n\n"
            "OPEN QUESTIONS FOR MAIN AGENT:\n"
            "- [anything unresolved that the main agent must decide based on what it finds]\n\n"
            "FINAL RECOMMENDATION: [one sentence — the single most important thing to do first]"
        ),
    },
]

# ─────────────────────────────────────────────────────────────────────────────
#  SECURITY ENGINEER VARIANT  — swap EXPERTS above with this for security work:
#
# EXPERTS = [
#   {"name":"RED",       "role":"Offensive/Attacker",    ...system: attacker mindset prompt...},
#   {"name":"BLUE",      "role":"Defensive/Hardening",   ...system: detection & prevention prompt...},
#   {"name":"ARCHITECT", "role":"Threat Modeler",        ...system: systemic design risk prompt...},
#   {"name":"AUDITOR",   "role":"Code Reviewer (OWASP)", ...system: CVE pattern matching prompt...},
# ]
# ─────────────────────────────────────────────────────────────────────────────


class Think(Tool):
    """
    Multi-Agent Expert Thinking Tool for Agent Zero.

    Runs 4 sequential LLM calls — ALPHA → BETA → GAMMA → DELTA —
    where each expert sees all prior contributions before responding.
    DELTA synthesises a consensus plan the main agent then executes.

    Tool args:
        problem (str) : Full problem statement with all relevant context.
        rounds  (str) : Integer 1–3. How many full debate passes. Default "1".
                        Use "2" for very complex or highly ambiguous problems.
                        Round 2: experts revise their positions after seeing
                        DELTA's round-1 synthesis. (Costs 4 extra API calls.)
    """

    async def execute(self, problem: str = "", rounds: str = "1", **kwargs) -> Response:
        # ── Resolve arguments ────────────────────────────────────────────────
        if not problem:
            problem = self.args.get("problem", "") or self.message

        try:
            total_rounds = max(1, min(int(rounds), 3))   # hard cap at 3 rounds
        except (ValueError, TypeError):
            total_rounds = 1

        # ── Printers ────────────────────────────────────────────────────────
        heading = PrintStyle(bold=True, font_color="#c39bd3", padding=True)
        content = PrintStyle(font_color="#d2b4de", padding=False)
        divider = PrintStyle(font_color="#7f8c8d", padding=False)

        heading.print(
            f"🧠 Expert Panel starting | {total_rounds} round(s)\n"
            f"Problem: {problem[:140]}{'...' if len(problem) > 140 else ''}"
        )

        debate_log: list[str] = []       # entire shared history, shown to every expert
        round_syntheses: list[str] = []  # DELTA output after each round

        # ── Main debate loop ─────────────────────────────────────────────────
        for round_num in range(1, total_rounds + 1):

            divider.print(f"\n{'═'*55}\n  ROUND {round_num} / {total_rounds}\n{'═'*55}")

            # On round 2+, seed the debate with the previous round's synthesis
            if round_syntheses:
                recap = (
                    f"[DELTA's Round {round_num - 1} Synthesis — "
                    f"all experts should now refine their positions based on this]\n"
                    f"{round_syntheses[-1]}"
                )
                debate_log.append(recap)

            for expert in EXPERTS:
                await self.set_progress(
                    f"Round {round_num}/{total_rounds} — "
                    f"{expert['name']} ({expert['role']}) is thinking..."
                )

                # ── Build the human turn message ─────────────────────────────
                if debate_log:
                    panel_so_far = "\n\n---\n\n".join(debate_log)
                    human_content = (
                        f"TASK FOR THE PANEL:\n{problem}\n\n"
                        f"=== PANEL DISCUSSION SO FAR ===\n{panel_so_far}\n\n"
                        f"{'─'*40}\n"
                        f"It is now your turn, {expert['name']}. "
                        f"Build on, challenge, or refine what has been said above:"
                    )
                else:
                    human_content = (
                        f"TASK FOR THE PANEL:\n{problem}\n\n"
                        f"You are FIRST to speak. Give your analysis as {expert['name']}:"
                    )

                messages = [
                    SystemMessage(content=expert["system"]),
                    HumanMessage(content=human_content),
                ]

                # ── Single LLM call for this expert ──────────────────────────
                # background=True: uses the agent's configured model but skips
                # the main-loop rate-limiter and streaming callback.
                expert_response, _ = await self.agent.call_chat_model(
                    messages=messages,
                    background=True,
                )
                expert_response = expert_response.strip()

                # ── Append to shared debate log ───────────────────────────────
                entry = (
                    f"[{expert['name']} – {expert['role']} | Round {round_num}]\n"
                    f"{expert_response}"
                )
                debate_log.append(entry)

                # ── Terminal print ────────────────────────────────────────────
                heading.print(f"[{expert['name']}]  {expert['role']}  · Round {round_num}")
                content.print(expert_response)
                divider.print("─" * 50)

                # ── WebUI log entry ───────────────────────────────────────────
                self.agent.context.log.log(
                    type="tool",
                    heading=(
                        f"{expert['icon']} Expert Panel — "
                        f"{expert['name']} ({expert['role']}) · Round {round_num}"
                    ),
                    content=expert_response,
                    kvps={
                        "expert": expert["name"],
                        "role":   expert["role"],
                        "round":  str(round_num),
                    },
                )

            # DELTA is always last — capture its synthesis for next round
            round_syntheses.append(debate_log[-1])

        # ── Assemble final response ───────────────────────────────────────────
        full_transcript = "\n\n---\n\n".join(debate_log)
        final_synthesis = round_syntheses[-1]   # DELTA's last output

        result = (
            "## 🧠 Expert Panel Complete\n\n"
            f"**Rounds:** {total_rounds}  |  "
            f"**Experts:** {', '.join(e['name'] for e in EXPERTS)}\n\n"
            "---\n\n"
            "### Full Debate Transcript\n\n"
            f"{full_transcript}\n\n"
            "---\n\n"
            "### ✅ DELTA's Final Consensus  ← execute this plan\n\n"
            f"{final_synthesis}"
        )

        heading.print(
            "✅ Expert Panel complete — consensus ready.\n"
            "Main agent will now follow DELTA's plan."
        )
        return Response(message=result, break_loop=False)

    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool",
            heading=(
                f"icon://psychology "
                f"{self.agent.agent_name}: 🧠 Expert Team Thinking"
            ),
            content="",
            kvps=self.args,
            _tool_name=self.name,
        )
```

***

## File 2 — `prompts/agent.system.tool.think.md` *(new file)*

```markdown
### think
Engage a 4-expert internal thinking panel before acting on any complex task.
Runs **4 sequential LLM calls**: ALPHA (strategy) → BETA (critique) → GAMMA (implementation steps) → DELTA (synthesis).
Each expert reads all prior contributions. DELTA produces the final **CONSENSUS PLAN** you must execute.

**Use FIRST (before any other tool) when:**
- security analysis, vulnerability hunting, penetration testing, code audits
- multi-step coding tasks, architecture decisions, major refactors
- debugging hard or ambiguous errors
- any task where you are unsure of the best approach or sequence
- tasks with significant failure risk or irreversible consequences

**Do NOT use for:**
- simple factual lookups or single-command tasks
- trivial file reads, "list files", "what is X"
- when the user says "quick answer", "just do it", or "skip thinking"

args: `problem`, optional `rounds`
- `problem`: complete problem statement — include all context, code snippets, file paths, error messages, constraints
- `rounds`: `"1"` (default, 4 API calls) or `"2"` (8 API calls, experts revise after seeing round-1 synthesis). Use `"2"` only for highly ambiguous or deeply complex problems.

example:
~~~json
{
  "thoughts": [
    "This is a complex, security-critical task.",
    "I should run the expert panel before touching anything."
  ],
  "headline": "Convening 4-expert thinking panel",
  "tool_name": "think",
  "tool_args": {
    "problem": "Find all security vulnerabilities in this Flask login endpoint and produce a prioritised fix plan.\n\nCode:\ndef login():\n    user = request.form['user']\n    pwd  = request.form['pwd']\n    row  = db.execute(f'SELECT * FROM users WHERE user={user} AND pwd={pwd}')\n    if row: session['user'] = user",
    "rounds": "1"
  }
}
~~~

After the tool returns, read the **DELTA's Final Consensus** section and follow the **CONSENSUS PLAN** step-by-step using your normal tools. You may adapt individual steps if you discover new information during execution.
```

***

## File 3 — `prompts/agent.system.main.solving.md` *(replace in full)*

```markdown
## Problem solving

not for simple questions only tasks needing solving
explain each step in thoughts

0 outline plan
agentic mode active

## Expert Team Thinking — use FIRST on complex tasks

Before executing tools on any complex, multi-step, or risky task, call the **think** tool.
It runs 4 expert agents (ALPHA → BETA → GAMMA → DELTA) who debate back and forth,
each reading all prior contributions, producing a concrete CONSENSUS PLAN.
Execute that plan using your normal tools.

When to call think first:
- security analysis, code audits, vulnerability hunting
- architecture or design decisions
- hard debugging or ambiguous error diagnosis
- any task where the best approach is not immediately obvious
- tasks with significant failure risk

Do NOT call think for:
- simple single-step tasks ("run this command", "read this file")
- direct factual questions
- when user explicitly says to skip it

---

1 check memories solutions skills prefer skills

2 break task into subtasks if needed

3 solve or delegate
tools solve subtasks
you can use subordinates for specific subtasks
call_subordinate tool
use prompt profiles to specialize subordinates
never delegate full to subordinate of same profile as you
always describe role for new subordinate
they must execute their assigned tasks

4 complete task
focus user task
present results verify with tools
don't accept failure retry be high-agency
save useful info with memorize tool
final response to user
```

***

## How the Flow Works

```
User: "Find bugs in this codebase"
           │
           ▼
  Main Agent monologue() starts
           │
           ▼
  Agent sees: "complex task → use think first"
           │
           ▼
  ┌─────────────────────────────────────────────┐
  │  think tool called with problem=<task>      │
  │                                             │
  │  API call 1 → ALPHA   strategic analysis    │
  │       ↓ (ALPHA's output fed in)             │
  │  API call 2 → BETA    challenges ALPHA      │
  │       ↓ (both outputs fed in)               │
  │  API call 3 → GAMMA   concrete steps        │
  │       ↓ (all three fed in)                  │
  │  API call 4 → DELTA   CONSENSUS PLAN        │
  └─────────────────────────────────────────────┘
           │
           ▼
  Agent reads DELTA's CONSENSUS PLAN
           │
           ▼
  Executes plan step-by-step with normal tools
  (code_execution, browser, search, etc.)
```

***

## Customizing for Security Mode

To make the security engineer team permanent, replace the `EXPERTS` list at the top of `tools/think.py` with:

```python
EXPERTS = [
    {
        "name": "RED",
        "role": "Offensive / Attacker",
        "color": "#f1948a",
        "icon": "icon://bug_report",
        "system": (
            "You are RED, the Offensive Security expert. Think like an attacker. "
            "Identify every exploitable vulnerability, attack vector, and trust boundary violation. "
            "For each finding: name it, explain how it's exploited, and rate severity (Critical/High/Med/Low). "
            "End with: 'MOST CRITICAL ATTACK PATH: <one sentence>.'"
        ),
    },
    {
        "name": "BLUE",
        "role": "Defensive / Hardening",
        "color": "#aed6f1",
        "icon": "icon://security",
        "system": (
            "You are BLUE, the Defensive Security expert. Read RED's findings. "
            "For each vulnerability RED found: propose the specific code fix, config change, or control. "
            "Also identify any detection gaps — what logging or monitoring is missing. "
            "End with: 'QUICKEST WIN: <the single fix that reduces risk most in least time>.'"
        ),
    },
    {
        "name": "ARCHITECT",
        "role": "Threat Modeler",
        "color": "#a9dfbf",
        "icon": "icon://account_tree",
        "system": (
            "You are ARCHITECT, the Threat Modeling expert. Read RED and BLUE. "
            "Identify systemic design flaws — privilege escalation paths, broken trust hierarchies, "
            "dangerous data flows, insecure defaults. Think STRIDE. "
            "Propose architectural fixes, not just patches. "
            "End with: 'ROOT CAUSE: <the fundamental design decision that created these issues>.'"
        ),
    },
    {
        "name": "AUDITOR",
        "role": "Code Reviewer / OWASP",
        "color": "#f9e79f",
        "icon": "icon://fact_check",
        "system": (
            "You are AUDITOR, the Code Review and Compliance expert. "
            "Read all three experts above. Map every finding to its OWASP Top 10 category and CVE class. "
            "Check for: hardcoded secrets, insecure dependencies, unsafe deserialization, "
            "missing input validation, weak crypto. "
            "Produce the final CONSENSUS PLAN:\n\n"
            "CONSENSUS PLAN:\n1. [fix]\n2. [fix]\n...\n\n"
            "OWASP MAPPING:\n- [finding] → [OWASP category]\n\n"
            "PRIORITY ORDER: [most critical fix first]\n\n"
            "FINAL RECOMMENDATION: [one sentence]"
        ),
    },
]
```

***

## Security Engineer Demo

Send this to your Agent Zero instance to test it immediately:

```
Use expert thinking: find security issues in this code:

def login():
    user = request.form['user']
    pwd  = request.form['pwd']
    row  = db.execute(f"SELECT * FROM users WHERE u='{user}' AND p='{pwd}'")
    if row:
        session['user'] = user
        session['admin'] = row['is_admin']
```

Expected output in terminal + WebUI:
```
🧠 Expert Panel starting | 1 round(s)
[ALPHA]  Strategic Analyst  · Round 1
  → identifies SQL injection as core issue...

[BETA]   Devil's Advocate   · Round 1
  → challenges ALPHA: "you missed session fixation and no CSRF..."

[GAMMA]  Implementer        · Round 1
  → Step 1: Replace f-string with parameterized query
  → Step 2: Add CSRF token to form
  → Step 3: Regenerate session ID after login...

[DELTA]  Synthesizer & Judge · Round 1
  CONSENSUS PLAN:
  1. Replace db.execute(f"...") with db.execute("...", (user, pwd))
  2. Add flask_wtf CSRF protection
  3. Call session.regenerate() after successful auth
  4. Audit all other db.execute() calls in codebase
  ...
✅ Expert Panel complete — consensus ready.
```

Agent then executes the plan using real tools.
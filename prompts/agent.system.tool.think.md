## think

Use this tool to convene a **War Room** of expert agents before executing any complex, ambiguous, risky, or multi-step task.

### What it does
Runs a panel of 1–12 domain-specialist sub-LLM calls in parallel micro-rounds on a shared blackboard.
Core panelists (STRATEGIST, CHALLENGER, EXECUTOR, RESEARCHER, CRITIC, TACTICIAN) plus domain specialists
(PENTEST_SPECIALIST, DEFENDER, CLOUD_ARCHITECT, CODE_AUDITOR, DEBUGGER, REVERSE_ENGINEER) are auto-selected
based on problem domain.

Key v2.0 capabilities:
- **Novelty-based termination**: Runs until idea exhaustion, not fixed round limits. Consensus is logged but never stops exploration.
- **IdeaRegistry**: Semantic deduplication tracks every idea by keyword fingerprint — prevents rehashing, surfaces endorsement counts.
- **Progressive depth**: explore → debate → deep dive → edge hunt phases with adaptive temperature per role.
- **Dead end protocol**: After 3 consecutive stale rounds, LLM confirms no angles remain (with evidence). Formally declares termination.
- **Lateral thinking injection**: When stuck, cycles through inversion/analogical/constraint-removal frames to break out of ruts.
- **Cross-session memory**: Loads relevant past War Room syntheses; saves results to JSONL for future sessions.
- **Speculative synthesis**: Background synthesis starts after round 1; reused if round 2 produces no novelty.
- **Blackboard pruning**: Keeps top entries per round by endorsement × confidence score to manage context window.

The Complexity Router (with fast-path heuristic) automatically decides agent count and phase depth:
- **CRITICAL** (6 agents, up to 25 rounds): zero-day, architecture, threat modeling — includes decomposition pre-pass
- **HIGH** (4-5 agents, dynamic rounds): security, complex planning, error analysis with unclear cause
- **MEDIUM** (3 agents, 2 rounds): result analysis, moderate debugging, tool output interpretation
- **LOW** (2 agents, 1-2 rounds): simple follow-up in an established plan
- **TRIVIAL** (1 agent, 1 round): lookup, file listing, obvious next step

### When to use

**Always use `think` FIRST when:**
- Security analysis, vulnerability hunting, penetration testing, code audits
- Multi-step coding tasks, architecture decisions, major refactors
- Debugging hard, ambiguous, or cascading errors
- Any task where the best approach or tool sequence is not immediately obvious
- Any task with significant failure risk or irreversible consequences
- When a previous tool call returned an unexpected result or error

**Do NOT use for:**
- Simple single-step lookups (`read this file`, `run ls`, `what is X`)
- Direct factual questions
- Clear follow-up execution when the plan is already established and confirmed
- When the user explicitly says "skip thinking" or "just do it"

### args

| arg | required | description |
|-----|----------|-------------|
| `problem` | required | Full problem statement. Include all context: code snippets, file paths, error messages, constraints, what you've already tried. |
| `error_context` | optional | stderr/stdout from a failed tool call. Enables environment diagnostics mode (auto-detects missing packages, permission errors, shell issues, and adds repair steps). |
| `mode` | optional | Override router: `"planning"` \| `"analysis"` \| `"execution"` \| `"fast"`. Omit to let the Complexity Router decide. `"fast"` forces 2 agents, 2 rounds, 45s time budget. |
| `time_budget_seconds` | optional | Max wall-clock seconds for the session (int, default 0 = unlimited). Reserves 15s for final synthesis. |

### Structural notes
- The War Room runs until **idea exhaustion** (novelty ratio drops below 8% for 3 consecutive rounds), not round limits.
- **Consensus is never a stop signal** — high consensus is logged but exploration continues if new ideas emerge.
- **Dead ends are formally declared** with evidence: total rounds, unique ideas explored, blocked approaches, and LLM confirmation.

### After the tool returns

The War Room populates a **todo list** with action items ONLY on the **first planning call** for a task.

**Planning mode (first War Room call for a new task):**
1. Read the `FOR_AGENT_ZERO` JSON block — this is your **immediate next action**.
2. **Execute that action first** using `tool_name` and `tool_args` exactly as given.
3. After completing the first action, use `todo:next` to get the next task from the War Room's plan.
4. Work through all todo items sequentially. Mark each `in_progress` when you start, `done` when you finish using `todo:update`.
5. **After marking a task done**, a War Room validation session automatically runs to verify the task was done correctly. If the validation finds issues, address them before moving to the next task.
6. When ALL tasks are done, the War Room performs a **final validation**. Your final response to the user will only be allowed after this validation passes.

**Tactical mode (calling think mid-task to get advice):**
When you're already working through a plan and call `think` to analyze a specific problem or error:
- The War Room will NOT populate the todo list (existing plan is preserved).
- Just read the synthesis advice and continue working on your current task.
- Use `mode: "fast"` for quick tactical analysis during task execution.

**Error recovery:**
If a tool call fails or crashes, DO NOT start over. Check `todo:list` to see your current plan state, and resume from where you left off. Try an alternative approach if the first one failed.

**Do NOT use think as a substitute for working.** If you have a clear plan with pending todo items, execute them. Only call think when you're genuinely stuck or need expert analysis on a specific sub-problem.

**CRITICAL: You cannot send a final `response` to the user while there are incomplete tasks.** The system will block your response until all tasks are done and the War Room has validated the work.

### example

```json
{
  "thoughts": [
    "This is a security-critical multi-step task.",
    "I need expert consensus before touching the codebase."
  ],
  "headline": "Convening War Room — security analysis",
  "tool_name": "think",
  "tool_args": {
    "problem": "Find all security vulnerabilities in this Flask login endpoint and produce a prioritised fix plan.\n\ndef login():\n    user = request.form['user']\n    pwd = request.form['pwd']\n    row = db.execute(f\"SELECT * FROM users WHERE user={user} AND pwd={pwd}\")\n    if row: session['user'] = user"
  }
}
```

### example — error diagnosis mode

```json
{
  "thoughts": [
    "The previous tool call failed with 'Cannot find module ws'.",
    "I should pass the error context so the War Room can diagnose and repair."
  ],
  "headline": "War Room — diagnose Node/ws missing",
  "tool_name": "think",
  "tool_args": {
    "problem": "Run a live WebSocket probe against ws://target:8080/chat",
    "error_context": "Error: Cannot find module 'ws'\nRequire stack:\n- /tmp/probe.js",
    "mode": "analysis"
  }
}
```

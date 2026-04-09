## think

Use this tool to convene a **War Room** of expert agents before executing any complex, ambiguous, risky, or multi-step task.

### What it does
Runs a panel of 1–5 expert sub-LLM calls in parallel micro-rounds on a shared blackboard.
Each expert (STRATEGIST, CHALLENGER, EXECUTOR, RESEARCHER, CRITIC) reads all prior contributions before responding.
A separate **SYNTHESIZER** is always called last to produce a final, concrete, machine-readable decision.

The Complexity Router automatically decides agent count and round count:
- **HIGH** (4 agents, 3 rounds): security analysis, novel planning, architecture decisions, risky/irreversible actions, first encounter with a problem
- **MEDIUM** (3 agents, 2 rounds): result analysis, tool output interpretation, error diagnosis
- **LOW** (1 agent, 1 round): simple follow-up in an already established plan

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
| `mode` | optional | Override router: `"planning"` \| `"analysis"` \| `"execution"`. Omit to let the Complexity Router decide automatically. |

### After the tool returns

Read the `FOR_AGENT_ZERO` JSON block at the end of the response.
**Use `tool_name` and `tool_args` exactly as given for your next action.**
Do not paraphrase or say "I will do X" — emit the JSON tool call directly.
You may adapt individual steps only if you discover new information the panel did not have.

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

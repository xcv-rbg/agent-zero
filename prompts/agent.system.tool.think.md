### think
convene a War Room expert panel before acting on complex tasks.
fires 3 experts in parallel per round then a synthesizer produces the CONSENSUS PLAN.
each round: experts read the shared blackboard and cross-pollinate. final synthesis resolves disagreements.
args: `problem`, optional `rounds`, `preset`, `budget`
- `problem`: complete problem statement with all relevant context, code, error messages, constraints. include everything the experts need — they have NO other context
- `rounds`: `"1"` to `"4"`. default `"2"`. use `"3"` or `"4"` for deeply complex or multi-domain problems
- `preset`: `"general"` (strategist/challenger/executor + synthesizer) or `"security"` (red/blue/architect + auditor)
- `budget`: time budget in seconds, default `"90"`, max `"180"`

**ALWAYS call think FIRST when:**
- security analysis, vulnerability hunting, code audits, penetration testing
- multi-step coding tasks, architecture decisions, major refactors
- debugging hard, ambiguous, or recurring errors
- the best approach is unclear or multiple valid strategies exist
- significant failure risk, irreversible consequences, or production impact
- research-heavy tasks requiring deep domain knowledge
- you are stuck, looping, or your previous approach failed
- you suspect you might be hallucinating or making assumptions
- the task spans multiple tools, files, or systems
- you need to plan a complex investigation or analysis workflow

**call think AGAIN (mid-task) when:**
- you hit an unexpected blocker or dead end
- new information contradicts your current approach
- the task turned out more complex than initially estimated
- you've made 2+ attempts at something without success
- you need to pivot strategy after partial execution

**do NOT use for:**
- simple factual lookups, single-command tasks, trivial file reads
- when user explicitly says "quick", "just do it", or "skip thinking"
- tasks you've already solved successfully with the same approach before

**the output is your EXECUTION PLAN — follow it:**
after the tool returns, the CONSENSUS PLAN is your step-by-step blueprint.
execute each numbered step using your normal tools.
if you discover new information during execution, adapt individual steps but maintain the overall strategy.
if a step fails, consider calling think again with the new context.

example:
~~~json
{
  "thoughts": [
    "This is a complex security-critical task spanning multiple systems.",
    "I should run the War Room to get expert consensus before acting."
  ],
  "headline": "Convening War Room expert panel",
  "tool_name": "think",
  "tool_args": {
    "problem": "Find all security vulnerabilities in this Flask login endpoint and produce a prioritised fix plan.\n\nCode:\ndef login():\n    user = request.form['user']\n    pwd = request.form['pwd']\n    row = db.execute(f'SELECT * FROM users WHERE user={user} AND pwd={pwd}')\n    if row: session['user'] = user",
    "rounds": "2",
    "preset": "security"
  }
}
~~~

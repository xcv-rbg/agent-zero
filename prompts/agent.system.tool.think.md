# think

Convene an instant **War Room v3** — 3 specialists chat in parallel (WhatsApp-group style), then a synthesizer produces the **PLAN** you execute step by step. Entire run targets under 60 seconds.

## args
| arg | required | values | default |
|-----|----------|--------|---------|
| `problem` | **yes** | Full problem + context + all code / errors / constraints | — |
| `preset` | no | `general` · `security` | `general` |
| `rounds` | no | 1–3 | 2 |
| `budget` | no | seconds | 60 |

> Include **everything** the experts need — they have **no other context**.

---

## ALWAYS call think FIRST for:

- **ANY security / hacking / CTF / pentest / recon / OSINT task** — no exceptions, every single time
- **ANY non-trivial task the user brings** (more than one step or one command)
- Multi-step coding, architecture decisions, major refactors, code audits
- Debugging hard, ambiguous, or recurring errors
- When the best approach is unclear or multiple valid strategies exist
- Significant failure risk, irreversible actions, or production impact
- Research-heavy tasks requiring deep domain knowledge
- Tasks spanning multiple tools, files, or systems
- Any task where failing would be expensive in time or effort

## call think AGAIN mid-task when:

- You hit an unexpected blocker or dead end
- New information contradicts your current approach
- The task turned out more complex than initially estimated
- You have made 2+ attempts at something without success
- You need to pivot strategy after partial execution
- You are stuck or looping on the same approach

## do NOT call think for:

- Simple single-command tasks or trivial file reads
- Obvious factual lookups with a single-step answer
- When user explicitly says: "quick", "just do it", "skip thinking"
- Tasks you have already solved with the same approach in this session

---

## example (security)

```json
{
  "thoughts": "New security task — War Room first, always.",
  "tool_name": "think",
  "tool_args": {
    "problem": "Target: wss://example.io/ws. Accepts WS handshakes from arbitrary origins, returns HTTP 101 with authenticated session cookies. Goal: confirm CSWSH exploitability and enumerate all WS methods. JS bundle excerpt attached.",
    "preset": "security",
    "rounds": 2
  }
}
```

---

After `think` returns, the **PLAN / CALL** is your step-by-step blueprint.
Execute each numbered step with your normal tools.
If a step fails or you hit a blocker → call `think` again with updated context.

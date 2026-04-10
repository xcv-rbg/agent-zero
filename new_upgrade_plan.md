# War Room v2.0 — Complete Upgrade Blueprint

---

## Architectural Diagnosis First

Before upgrades, here are the **critical wiring failures** found in the current codebase:

1.  **`_20_warroom_inject.py` pops `warroom_post_tool` from `params_persistent` but nothing in the uploaded code ever sets it** — the referenced `_20_warroom_auto.py` is missing entirely. The injection wiring is broken at the seam.
    
2.  **The stopping condition is philosophically inverted for exploration**: `if consensus_score >= 0.70: break` — it stops the war room when agents *agree too much*. For exhaustive exploration, agreement should *never* be the stop signal. Only idea exhaustion should stop it.
    
3.  **`_cached_war_llm` is set on `self` which is a new Tool instance per call** — the "cache" actually persists only within a single `execute()` invocation, not across calls. It rebuilds every time `think` is invoked.
    
4.  **Panelist blackboard rendering is lossy** — `_render_blackboard()` truncates structured data to 100 chars, discarding reasoning that later-round panelists need to properly challenge.
    
5.  **Synthesizer's `for_agent_zero` is never schema-validated** — a malformed `tool_args` silently passes through and causes the main agent to fail downstream.
    

---

## The Core Paradigm Shift: Stop Condition Inversion

```text
v1.0 Logic:  STOP when panelists AGREE  (consensus ≥ 0.70)
v2.0 Logic:  STOP when no NEW IDEAS appear for N consecutive rounds
```

This single inversion is the philosophical foundation of v2.0. Everything else is built on top of it.

---

## 20 Major Upgrades — Full Blueprints

---

### UPGRADE 1 — Infinite Exploration Loop with Novelty-Based Termination

**Category: Core Architecture | Impact: Foundational**

**Problem:** `max_rounds` is hardcoded (max 3). The loop breaks on consensus. Once 3 rounds finish the war room is done regardless of whether every approach has been attempted. This is the single biggest limitation.

**What to do:** Replace the `for round_num in range(1, max_rounds + 1)` loop with a `while` loop that runs indefinitely until a dead-end condition is confirmed. Remove `max_rounds` as a termination criterion. Keep it only as a safety ceiling (e.g., 25 rounds) to prevent pathological runaway.

**How to do it:**

In `think.py`, replace the round loop block with this logic structure:

```text
DEAD_END_THRESHOLD = 3        # consecutive stale rounds to confirm dead end
SAFETY_CEILING = 25           # absolute maximum rounds regardless
NOVELTY_FLOOR = 0.08          # minimum fraction of genuinely new ideas per round

stale_streak = 0
round_num = 0
idea_registry = IdeaRegistry()  # see Upgrade 2

while stale_streak < DEAD_END_THRESHOLD and round_num < SAFETY_CEILING:
    round_num += 1
    
    round_entries = fire_parallel_round(round_num)
    new_idea_count = idea_registry.register_and_count_novel(round_entries)
    novelty_ratio = new_idea_count / max(len(round_entries), 1)
    
    if novelty_ratio < NOVELTY_FLOOR:
        stale_streak += 1
        if stale_streak == 2:
            lateral_injected = inject_lateral_thinking_prompt(idea_registry)
            if lateral_injected:
                stale_streak = 1  # partial reset — give it one more shot
    else:
        stale_streak = 0  # still fresh, keep going
    
    blackboard.append(round_entries)
    
    # Do NOT break on consensus — consensus is no longer the stop signal
    # Periodically checkpoint (see Upgrade 18)
    if round_num % 5 == 0:
        run_checkpoint_synthesis(blackboard)

# Declare dead end
emit_dead_end_declaration(idea_registry, round_num)
run_final_synthesis(blackboard)
```

**Where to do it:** `think.py` → `execute()` method, the round loop section (~line 185–230 in current code).

**Speed note:** This doesn't slow anything down for simple problems — a trivial TRIVIAL-routed problem still gets 1 round and exits cleanly when the single round produces no novel ideas. The infinite loop only extends sessions when genuine novelty is being found.

---

### UPGRADE 2 — Idea Registry with Semantic Deduplication

**Category: Core Architecture | Impact: Foundational**

**Problem:** Panelists recycle the same ideas with different wording across rounds. Round 2 "use sqlmap with --level 5" and Round 3 "run sqlmap at maximum depth" are the same idea. Currently both get added to the blackboard and counted toward progress. The war room spins thinking it's making progress when it's just paraphrasing.

**What to do:** Build an `IdeaRegistry` class within `think.py` that fingerprints every panelist output and tracks genuine novelty.

**How to do it:**

Create a new class `IdeaRegistry` in `think.py` above the `Think` class:

```text
class IdeaRegistry:
    Fields:
        - ideas: dict[str, dict]          # fingerprint → full entry
        - explored_angles: list[str]       # short labels of explored approaches
        - pending_angles: list[str]        # angles mentioned but not yet pursued
        - blocked_angles: dict[str, str]   # angle → reason it was ruled out
        - idea_endorsements: dict[str, int]  # fingerprint → how many panelists cited it
    
    Methods:
    
    _fingerprint(text):
        # Extract 6-8 high-information keywords (nouns + verbs, strip stopwords)
        # Sort them deterministically
        # Return frozenset as fingerprint key
        # This is the cheap path — no embeddings needed
    
    is_novel(text):
        candidate = _fingerprint(text)
        for existing_fp in self.ideas:
            overlap = len(candidate & existing_fp) / max(len(candidate), 1)
            if overlap > 0.65:   # 65% keyword overlap = same idea
                return False
        return True
    
    register_and_count_novel(round_entries):
        # For each entry, extract suggested_action + position
        # Check is_novel() against registry
        # New → add to ideas, add angle label to explored_angles
        # Not novel → increment endorsement count for matching idea
        # Return: count of genuinely new ideas this round
    
    get_unexplored_angles(blackboard):
        # Scan all panelist text for phrases like "we could also...", "another approach..."
        # Extract candidate angles not yet in explored_angles
        # Return list of unexplored angles for Upgrade 3
    
    get_summary():
        # Return structured summary: N ideas, top ideas, pending angles
        # Used by synthesizer and checkpoint synthesis
```

**Where to do it:** New class in `think.py`. Instantiated at the start of `execute()`. Passed into `_run_parallel_round()` and `_run_one_panelist()`. Its summary is injected into the blackboard header.

**Blackboard injection:** At the start of every round prompt (in `_run_one_panelist`), prepend:

```text
IDEA REGISTRY STATE (do NOT repeat these, they are exhausted):
- Explored: [list of explored angle labels]

- Pending/Unexplored: [list of pending angles — explore these]
- Blocked: [angle: reason]

Your job: contribute ideas NOT on the Explored list.
If you cannot find a new angle, say explicitly: "I have no new approaches to add."
```

This single prompt change is what transforms the war room from a recycling machine into a genuine explorer.

---

### UPGRADE 3 — Unexplored Angles Tracker + Formal Dead End Protocol

**Category: Core Architecture | Impact: High**

**Problem:** No mechanism distinguishes "we've thought of everything" from "we gave up early." Dead ends need to be formally declared with evidence, not just caused by hitting a round limit.

**What to do:** Add explicit angle tracking as a first-class concern. Add a `DeadEndDeclaration` to the synthesis output when exhaustion is confirmed.

**How to do it:**

**Angles tracker** lives inside `IdeaRegistry` (see Upgrade 2). At the end of each round, after all panelists have fired, run a fast "angle extraction" pass:

```text
Extract unexplored angles by prompting all entries:
  "List any approaches or angles mentioned by any panelist 
   that we have NOT fully explored yet. One per line, very short."

This is a single LLM call (temperature=0.1) on the concatenated round output.
Parse the output into pending_angles list.
```

**Dead end confirmation sequence:** When `stale_streak` reaches `DEAD_END_THRESHOLD`:

1.  Run a "Dead End Confirmation" LLM call with this prompt:
    
    ```text
    IDEA REGISTRY: [full summary]
    PENDING ANGLES: [list]
    
    Question: Are there ANY unexplored approaches, tools, techniques, 
    pathways, edge cases, or sub-problems we have NOT addressed?
    Answer ONLY: YES [specific angle] or NO [brief reason why exhausted]
    ```
    
2.  If response contains YES → extract the angle, add to pending\_angles, reset `stale_streak` to 1
    
3.  If response contains NO → true dead end declared. Set a `dead_end_confirmed = True` flag.
    

**Dead End Declaration in output:** When `dead_end_confirmed`, the verbose log and compact result both include:

```text


## EXPLORATION COMPLETE — Dead End Reached
- Total rounds: N

- Total unique ideas explored: M
- Blocked approaches: [list with reasons]

- Explored angles: [complete list]
- Reason for termination: [LLM's explanation]
```

**Where:** `think.py` → `execute()`, new method `_confirm_dead_end()`, modifications to `_format_compact_result()` and `_format_verbose_log()`.

---

### UPGRADE 4 — Lateral Thinking Injector (Unstick Mechanism)

**Category: Exploration Quality | Impact: High**

**Problem:** When consecutive stale rounds occur, the war room currently has no mechanism to "unstick" and find new angles. It just keeps firing the same panelists with the same framing.

**What to do:** When `stale_streak == 2`, before firing the next round normally, inject a "lateral thinking" seed prompt that forces panelists outside their current frame.

**How to do it:**

Create a new method `_build_lateral_injection(idea_registry)` in `think.py`:

```text
Lateral seed prompts (cycle through these when stale):
1. INVERSION: "Assume every approach explored so far is completely wrong. 
               What would be true? What approach would work in that world?"

2. ANALOGICAL: "This problem is similar to [paraphrase problem]. 
               What techniques from a completely unrelated domain 
               (physical security, supply chain, social engineering, 
               hardware, business logic) could apply?"

3. CONSTRAINT REMOVAL: "If there were no restrictions — no detection risk, 
                        no time limit, unlimited access — what would you try?"

4. MINIMUM FOOTPRINT: "What is the absolute smallest, simplest possible action 
                       that could give us meaningful information or progress?"

5. FAILURE ANALYSIS: "What would cause every approach we've tried to fail 
                      simultaneously? What's the common dependency we're missing?"
```

At `stale_streak == 2`, pick the next lateral seed (cycle index = `stale_streak // DEAD_END_THRESHOLD`), prepend it to all panelist human prompts for the next round. This changes the framing without changing the panelist personas.

Track: which laterals have been used. Don't repeat the same lateral twice.

**Speed note:** Zero overhead — no extra LLM calls. It's just a prompt prefix added to the existing parallel round.

**Where:** `think.py` → new method `_build_lateral_injection()`, called in `execute()` before `_run_parallel_round()` when `stale_streak == 2`.

---

### UPGRADE 5 — Heuristic Fast-Path Router

**Category: Speed | Impact: High — eliminates 1 LLM call for ~60% of invocations**

**Problem:** Every `think()` call spends one full LLM inference call on the complexity router. For predictable task types (file listing, known security patterns, explicit coding tasks) this is pure overhead.

**What to do:** Before calling the LLM router, run a regex/keyword heuristic that handles the obvious cases instantly. Only fall through to LLM for ambiguous problems.

**How to do it:**

Create `_fast_route(problem: str, mode_override: str) -> dict | None` in `think.py`:

```python
TRIVIAL_PATTERNS = [
    r"\b(ls|cat|pwd|echo|grep|find|head|tail|which)\b",
    r"what is \w+",
    r"read (the |this )?(file|content)",
    r"list (files|directory)",
    r"print.{0,30}variable",
]

CRITICAL_PATTERNS = [
    r"\b(zero.?day|0day|RCE|remote code|privilege escalat|initial access)\b",
    r"\b(attack (chain|surface|vector)|threat model|red team|APT)\b",
    r"\b(architecture|system design|multi.?stage)\b",
    r"(full|complete) (pentest|assessment|audit|compromise)",
]

HIGH_PATTERNS = [
    r"\b(CVE-\d{4}-\d+|sql.?inject|XSS|SSRF|XXE|deseri|buffer overflow)\b",
    r"\b(vulnerabilit|exploit|bypass|escalat|inject)\b",
    r"\b(debug|diagnose|traceback|exception|error|crash)\b",
    r"\b(refactor|redesign|migrate|integrate)\b",
]

Logic:
1. Check TRIVIAL_PATTERNS → return {complexity: TRIVIAL, agent_count: 1, rounds: 1, mode: execution}
2. Check CRITICAL_PATTERNS → return {complexity: CRITICAL, agent_count: 6, rounds: 3, mode: planning}
3. Check HIGH_PATTERNS → return {complexity: HIGH, agent_count: 4, rounds: 2, mode: planning}
4. No match → return None (fall through to LLM router)
```

Additionally: Cache the last 20 router results keyed by the first 200 chars of problem text (MD5 hash). If the same problem re-enters (e.g., retry after error), skip routing entirely.

**Where:** `think.py` → `_run_router()` method. Add `fast = self._fast_route(problem, mode_override)` as the first line. If `fast` is not None, return it immediately.

**Speed gain:** For a typical session, this eliminates 60%+ of router LLM calls. A router call typically costs 300-600ms. Over a 10-call session that's 2-3 seconds saved.

---

### UPGRADE 6 — Domain-Adaptive Panelist Library (12 Specialist Personas)

**Category: Intelligence Quality | Impact: High**

**Problem:** The same 6 generic personas handle web security, malware reverse engineering, cloud architecture, and Python debugging identically. A "RESEARCHER" persona gives very different quality output discussing cloud IAM versus discussing heap overflow mitigations.

**What to do:** Expand the panelist library from 6 to 12 domain specialists. The router (or a new domain classifier) selects the optimal 6 based on problem domain. The generic panelists (`STRATEGIST`, `EXECUTOR`, etc.) remain as fallbacks.

**How to do it:**

Add these domain specialists to `_ALL_PANELISTS` in `think.py`:

```text
PENTEST_SPECIALIST:
  Persona: Offensive security expert with 15 years of red team ops
  Focus: Kill chain, initial foothold, lateral movement, persistence
  Special: Always proposes the most direct path to objective

DEFENDER:
  Persona: Blue team architect specializing in detection engineering  
  Focus: What artifacts will our actions leave, what will defenders see
  Special: Forces adversarial actions to be evasion-aware

CLOUD_ARCHITECT:
  Persona: AWS/GCP/Azure specialist, IAM expert
  Focus: Cloud-native service abuse, misconfiguration patterns, blast radius
  
CODE_AUDITOR:
  Persona: Static analysis specialist, source code review expert
  Focus: Data flow, trust boundaries, deserialization paths, taint analysis
  
DEBUGGER:
  Persona: Systems debugging expert, tracing, profiling
  Focus: Root cause analysis, reproduction steps, minimal repro cases
  
REVERSE_ENGINEER:
  Persona: Binary analysis, firmware, protocol reverse engineering
  Focus: Tooling (Ghidra, Frida, Wireshark), pattern identification
```

Add a domain classifier layer to `_run_router()`. After getting complexity/agent\_count from the fast-path or LLM router, run a fast keyword domain classification:

```text
domain_map = {
    "web|http|api|jwt|cookie|session|xss|sqli":           ["PENTEST_SPECIALIST", "CODE_AUDITOR"],
    "cloud|aws|gcp|azure|s3|iam|lambda|kubernetes":       ["CLOUD_ARCHITECT", "PENTEST_SPECIALIST"],
    "binary|firmware|elf|pe|ghidra|frida|assembly":       ["REVERSE_ENGINEER", "EXECUTOR"],
    "debug|traceback|exception|crash|segfault":           ["DEBUGGER", "CODE_AUDITOR"],
    r"detect|alert|siem|log|artifact|edr":                ["DEFENDER", "CRITIC"],
}

# Replace 1-2 generic panelists with domain specialists where applicable
```

**Where:** `think.py` → `_ALL_PANELISTS` list (expand), `_PANELIST_SUBSET` dict (add domain selection logic), `_run_router()` (add domain classifier).

---

### UPGRADE 7 — Progressive Depth Round Architecture

**Category: Intelligence Quality | Impact: High**

**Problem:** All rounds use the same prompt template regardless of round number. Round 1 should explore broadly. Round 2 should debate and challenge. Round 3+ should drill into specifics of the winning approach and find edges. Currently all rounds say "respond with your position" and get the same depth.

**What to do:** Create 3 distinct round phase templates that get applied based on round number.

**How to do it:**

In `_run_one_panelist()`, replace the generic `round_num` prompt suffix with phase-specific instructions:

```text
PHASE_PROMPTS = {
    "explore": (  # round_num == 1
        "EXPLORE PHASE — Round 1.\n"
        "This is ideation. Be creative and broad. Name ALL viable approaches "
        "you can think of. Don't commit to one — scan the entire solution space. "
        "Aim for novelty: what approach would someone miss on first reading?\n"
        "Include: unconventional paths, non-obvious dependencies, things that "
        "look impossible but might not be."
    ),
    "debate": (  # round_num == 2-3
        "DEBATE PHASE — Round {n}.\n"
        "The group has had its opening positions. Now CHALLENGE and REFINE.\n"
        "MANDATORY: Name at least one other panelist by role (e.g. 'STRATEGIST's "
        "approach has a flaw:...'). Explain what changed in your assessment "
        "and WHY. If you agree, say what specifically convinced you. "
        "If you disagree, propose a concrete alternative — not a vague objection."
    ),
    "deep_dive": (  # round_num >= 4
        "DEEP DIVE PHASE — Round {n}.\n"
        "A leading approach has emerged. Now find its edges and failure modes.\n"
        "Focus: What breaks this? What's the hardest part to execute? "
        "What assumption, if wrong, would make the whole approach fail? "
        "What does success look like exactly — what's the verification step?"
    ),
    "edge_hunt": (  # round_num >= 7 (infinite mode only)
        "EDGE HUNT PHASE — Round {n}.\n"
        "We've explored the main paths. Hunt for the non-obvious.\n"
        "Look at: race conditions, timing attacks, encoding edge cases, "
        "language/platform quirks, dependency chains, business logic bypass, "
        "second-order effects. What have we collectively been too focused to notice?"
    ),
}

Phase selection:
  round_num == 1 → "explore"
  round_num in [2, 3] → "debate"
  round_num in [4, 5, 6] → "deep_dive"
  round_num >= 7 → "edge_hunt"
```

**Temperature alignment** (see also Upgrade 9): `explore` phase uses temperature 0.7, `debate` uses 0.35, `deep_dive` uses 0.2, `edge_hunt` uses 0.4 (slightly higher to find weird edges).

**Where:** `think.py` → `_run_one_panelist()` — replace the `round_num` conditional near the end of `human_content` construction.

---

### UPGRADE 8 — Anti-Sycophancy: Mandatory Contrarian Voice

**Category: Intelligence Quality | Impact: Medium-High**

**Problem:** In round 2+, panelists read prior rounds on the blackboard and tend to converge toward the dominant early position. This is LLM sycophancy at scale — the most confident early responder disproportionately shapes all subsequent output. Real expert panels don't work this way.

**What to do:**

1.  In every round 2+, assign one panelist (rotating) the explicit CONTRARIAN role as a prompt overlay.
2.  Add a new `DEVIL` persona variant that is permanently adversarial.

**How to do it:**

In `_run_parallel_round()`, before building the tasks list for rounds 2+:

```python


# Identify the "dominant position" from the blackboard


# (the suggested_action with highest endorsement count from idea_registry)
dominant_position = idea_registry.get_most_endorsed_action()

# Pick the CHALLENGER panelist, or if not present, the last panelist in list
contrarian_target = next(
    (p for p in panelists if p["name"] == "CHALLENGER"), 
    panelists[-1]
)

# Build a contrarian overlay dict — doesn't replace the panelist, just prepends
contrarian_overlay = {
    "prefix": (
        f"MANDATORY CONTRARIAN ROLE FOR THIS ROUND:\n"
        f"The dominant emerging position is: '{dominant_position}'\n"
        f"Your ONLY job this round is to find reasons this is WRONG or INCOMPLETE.\n"
        f"Do NOT agree with this position. Find the attack on it.\n"
        f"If you genuinely cannot find a flaw, say exactly: "
        f"'No valid objection found — approach is sound because [specific reason]'\n\n"
    )
}

# Pass contrarian_overlay to _run_one_panelist for that target panelist
```

Add a fourth stopping condition to avoid infinite contrarianism:

```text
If the contrarian panelist has said "No valid objection found" for 3 consecutive rounds
→ add that angle to blocked_angles with reason "Survived sustained challenge"
→ de-escalate contrarian role
```

**Where:** `think.py` → `_run_parallel_round()` (round selection logic), `_run_one_panelist()` (accept optional `contrarian_prefix` parameter, prepend to human content).

---

### UPGRADE 9 — Adaptive Temperature Schedule

**Category: Speed + Quality | Impact: Medium**

**Problem:** Static `temperature=0.25` for all panelists in all rounds is a poor tradeoff. Exploration needs high temperature (creative breadth). Convergence needs low temperature (precise, deterministic). The synthesizer at 0.1 is correct but everything else is the same flat value.

**What to do:** Map temperature to round phase and panelist role.

**How to do it:**

Create a temperature lookup in `think.py`:

```python
TEMPERATURE_MAP = {
    # (phase, role) → temperature
    ("explore",    "STRATEGIST"):   0.75,
    ("explore",    "CHALLENGER"):   0.80,
    ("explore",    "EXECUTOR"):     0.50,  # executor stays grounded
    ("explore",    "RESEARCHER"):   0.65,
    ("explore",    "CRITIC"):       0.60,
    ("explore",    "TACTICIAN"):    0.85,  # highest creative for attack paths
    
    ("debate",     "*"):            0.35,
    
    ("deep_dive",  "*"):            0.20,
    ("edge_hunt",  "*"):            0.45,  # slightly higher to find weird edges
    
    # Synthesizer always deterministic
    ("synthesis",  "SYNTHESIZER"):  0.05,
    ("router",     "*"):            0.00,  # router must be deterministic
}

def get_temperature(phase: str, role: str) -> float:
    return (
        TEMPERATURE_MAP.get((phase, role))
        or TEMPERATURE_MAP.get((phase, "*"))
        or 0.25  # fallback
    )
```

Pass `phase` through `_run_parallel_round()` → `_run_one_panelist()` → `_llm_call()`.

**Speed note:** Higher temperature in early rounds paradoxically speeds up convergence by finding the right angle faster — fewer rounds needed to exhaust the idea space.

---

### UPGRADE 10 — Speculative Background Synthesis

**Category: Speed | Impact: Medium — hides synthesis latency**

**Problem:** The synthesizer runs sequentially after all rounds complete. A typical synthesizer call costs 600–1500ms. This adds directly to wall-clock time.

**What to do:** After round 1 completes, start a speculative synthesis in the background. When the main rounds finish, check if the speculative synthesis is still valid (blackboard didn't change substantially). If valid, use it — synthesis latency = 0. If invalid (rounds changed the direction significantly), discard and run fresh.

**How to do it:**

In `execute()`, after the first round completes and as the second round fires:

```python


# Fire second round AND speculative synthesis simultaneously
speculative_synth_task = asyncio.create_task(
    self._run_synthesizer(
        problem=problem,
        blackboard=blackboard_after_round_1,
        consensus_score=consensus_score_round_1,
        env_hints=env_hints,
    )
)

# Continue with round 2...
round_2_entries = await _run_parallel_round(round_num=2, ...)
blackboard.append(round_2_entries)

# Check if speculative synthesis is still relevant
coverage_delta = idea_registry.novelty_delta(
    blackboard_after_round_1, 
    blackboard  # now includes round 2
)

if coverage_delta < 0.20:  # round 2 didn't change much (< 20% new ideas)
    speculative_raw = await speculative_synth_task  # already done
    synthesis = self._safe_json(speculative_raw)
    synthesis_raw = speculative_raw
    # skip the fresh synthesizer call — use speculative
else:
    speculative_synth_task.cancel()
    synthesis_raw = await self._run_synthesizer(...)  # fresh call
```

**Speed gain:** In cases where round 1 produces strong enough signal (common for MEDIUM-complexity problems), synthesis is already done by the time round 2 finishes. Net saving: full synthesizer latency (~600–1200ms).

**Where:** `think.py` → `execute()`, the section after the first round completes.

---

### UPGRADE 11 — Synthesizer Output Validation with Auto-Retry

**Category: Main Agent Compliance | Impact: High**

**Problem:** `_safe_json()` returns `{}` on parse failure, and the compact result silently falls back to `synthesis_raw[:600]` — raw LLM text, not a tool call. The main agent sees prose instead of a JSON directive and has nothing to execute. This is a silent compliance failure.

**What to do:** After parsing synthesis output, run a structured validation. If validation fails, re-prompt the synthesizer with the specific error. Max 2 retries.

**How to do it:**

Create `_validate_synthesis(synthesis: dict) -> list[str]` in `think.py`:

```python
VALID_TOOL_NAMES = {
    "code_execution", "browser_open", "browser_do", "search_engine",
    "document_query", "skills_tool", "call_subordinate", 
    "memory_tool", "response", "think"
}

TOOL_REQUIRED_ARGS = {
    "code_execution": ["runtime", "code"],
    "browser_open":   ["url"],
    "browser_do":     ["action"],
    "search_engine":  ["query"],
    "skills_tool":    ["action"],
    "memory_tool":    ["action"],
    "call_subordinate": ["message"],
    "response":       ["text"],
}

def _validate_synthesis(synthesis: dict) -> list[str]:
    errors = []
    faz = synthesis.get("for_agent_zero", {})
    if not faz:
        errors.append("Missing for_agent_zero block entirely")
        return errors
    
    tool_name = faz.get("tool_name", "")
    if tool_name not in VALID_TOOL_NAMES:
        errors.append(f"Invalid tool_name '{tool_name}'. Must be one of: {VALID_TOOL_NAMES}")
    
    tool_args = faz.get("tool_args", {})
    if not isinstance(tool_args, dict) or not tool_args:
        errors.append("tool_args is empty or not a dict")
    else:
        required = TOOL_REQUIRED_ARGS.get(tool_name, [])
        for req in required:
            if req not in tool_args or not tool_args[req]:
                errors.append(f"tool_args missing required field '{req}' for {tool_name}")
    
    if not faz.get("thoughts"):
        errors.append("thoughts array is empty — synthesizer must explain reasoning")
    
    if not faz.get("headline"):
        errors.append("headline is missing")
    
    return errors
```

In `_run_synthesizer()`, wrap the call in a retry loop:

```python
for attempt in range(3):  # max 2 retries
    raw = await self._llm_call(msgs, temperature=0.05)
    synthesis = self._safe_json(raw)
    errors = self._validate_synthesis(synthesis)
    
    if not errors:
        return raw  # valid
    
    if attempt == 2:
        break  # give up, return best effort
    
    # Re-prompt with specific error
    correction_prompt = (
        f"Your previous output had these errors:\n"
        + "\n".join(f"- {e}" for e in errors)
        + "\n\nFix ONLY these errors. Output the corrected JSON."
    )
    msgs.append(HumanMessage(content=correction_prompt))
    msgs.append(SystemMessage(content="Output ONLY the corrected JSON. No prose."))
```

**Where:** `think.py` → new method `_validate_synthesis()`, modifications to `_run_synthesizer()`.

---

### UPGRADE 12 — Cross-Session Memory Integration

**Category: Intelligence Quality | Impact: Medium-High**

**Problem:** Each think() call starts with zero knowledge of what was explored in past War Room sessions on the same problem type. The agent\_memory tool exists and is already in the Agent Zero toolkit — the War Room should use it.

**What to do:** At War Room session start, query `memory_tool` for similar past problems. Inject top 3 relevant past syntheses into the initial blackboard as "Historical Record." After final synthesis, save the session to memory.

**How to do it:**

In `execute()`, add a pre-flight memory load step before the round loop:

```python


# Step -1: Load historical War Room memory
historical_context = await self._load_historical_context(problem)
if historical_context:
    # Inject as round 0 on the blackboard — panelists can see it
    blackboard.insert(0, {
        "round": "historical",
        "entries": [{
            "agent": "MEMORY",
            "role": "Historical Record",
            "round": "historical",
            "raw": historical_context,
            "structured": {
                "position": "Prior War Room findings for similar problems",
                "suggested_action": historical_context[:400],
                "key_risk": "Historical context may be stale or inapplicable",
                "confidence": 0.6,
            }
        }]
    })
```

Create `_load_historical_context(problem: str) -> str` method:

```python
async def _load_historical_context(self, problem: str) -> str:
    try:
        # Use the agent's memory tool to query for similar problems
        # Extract top 2 keywords from problem as query terms
        keywords = extract_keywords(problem, n=3)
        query = f"war_room_synthesis {' '.join(keywords)}"
        
        result = await self.agent.call_tool(
            "memory_tool",
            {"action": "query", "query": query, "count": 3}
        )
        return result[:800] if result else ""
    except Exception:
        return ""  # fail silently — historical context is optional
```

After final synthesis, save to memory:

```python
async def _save_to_memory(self, problem: str, synthesis: dict, 
                          idea_registry: IdeaRegistry):
    save_content = {
        "war_room_synthesis": True,
        "problem_summary": problem[:200],
        "consensus_action": synthesis.get("consensus_action"),
        "explored_angles": idea_registry.explored_angles,
        "blocked_angles": idea_registry.blocked_angles,
        "confidence": synthesis.get("confidence"),
    }
    try:
        await self.agent.call_tool(
            "memory_tool", {
                "action": "save",
                "content": json.dumps(save_content),
                "tags": ["war_room", "synthesis"],
            }
        )
    except Exception:
        pass  # saving to memory is best-effort
```

**Where:** `think.py` → `execute()` (pre-round step and post-synthesis step), two new helper methods.

---

### UPGRADE 13 — Idea Momentum Scoring

**Category: Intelligence Quality | Impact: Medium**

**Problem:** Good ideas can get buried. If STRATEGIST proposes approach X in round 1 and every other panelist mentions it in passing across rounds 2-4 but CHALLENGER keeps dominating the conversation with a flawed alternative, approach X will be underrepresented in the blackboard rendering and the synthesizer may overlook it.

**What to do:** Track how many panelists explicitly reference each idea across all rounds. Surface high-momentum ideas explicitly to the synthesizer.

**How to do it:**

In `IdeaRegistry.register_and_count_novel()`, when a submitted idea is NOT novel (it matches an existing fingerprint), increment that idea's endorsement counter (`idea_endorsements[fingerprint] += 1`).

At the start of the synthesizer prompt, prepend:

```python


# Get top-N most endorsed ideas
top_ideas = idea_registry.get_top_ideas(n=5)
momentum_section = (
    "\n\nHIGH-MOMENTUM IDEAS (referenced most frequently by panelists — "
    "give these extra weight in your synthesis):\n"
    + "\n".join(
        f"{i+1}. [{idea['agent']} × {idea['endorsements']} refs] "
        f"{idea['text'][:200]}"
        for i, idea in enumerate(top_ideas)
    )
)
```

Also: Add momentum to the verbose log output so users can see which ideas the panel collectively converged on even across rounds.

**Where:** `think.py` → `IdeaRegistry` class (endorsement tracking), `_run_synthesizer()` (momentum injection).

---

### UPGRADE 14 — Main Agent Compliance: Fix the Broken Wiring

**Category: Main Agent Compliance | Impact: Critical**

**Problem:** `_20_warroom_inject.py` pops `warroom_post_tool` from `params_persistent`, but nothing in the uploaded code sets it. The reference to `_20_warroom_auto` (which would set it) is missing. This means the injection extension silently does nothing — the War Room's post-analysis never reaches the main agent's next prompt.

**What to do:** Create the missing `_20_warroom_auto.py` extension AND fix the injection chain.

**Specifically where:**

**A) Create `extensions/python/message_loop_prompts_after/_20_warroom_auto.py`:**

This extension runs after every tool call. When it detects that a tool called was `think`, it captures the tool response and stores it for injection:

```text
Class: WarRoomAutoCapture(Extension)
File: extensions/python/message_loop_prompts_after/_20_warroom_auto.py

execute(loop_data: LoopData):
    # Get the most recent tool result
    last_tool = loop_data.params_temporary.get("last_tool_name", "")
    last_result = loop_data.params_temporary.get("last_tool_response", "")
    
    if last_tool == "think" and last_result:
        # Store for injection into NEXT prompt (persistent survives temp reset)
        loop_data.params_persistent["warroom_post_tool"] = last_result
        loop_data.params_persistent["warroom_tool_call_count"] = (
            loop_data.params_persistent.get("warroom_tool_call_count", 0) + 1
        )
```

**B) Strengthen `_20_warroom_inject.py` — inject BEFORE the system messages:**

The current injection adds to `extras_temporary["warroom_post_tool_analysis"]`. But if this key isn't rendered by Agent Zero's `prepare_prompt()`, it silently vanishes. Verify where `extras_temporary` is consumed in `agent.py` and if necessary, inject directly into `loop_data.messages` as a `SystemMessage` instead:

```python


# Stronger injection: prepend a SystemMessage (not just extras_temporary)
from langchain_core.messages import SystemMessage

warroom_msg = SystemMessage(content=(
    "\n\n⚠️ WAR ROOM DIRECTIVE — MANDATORY EXECUTION:\n"
    + str(analysis)[:3000]
    + "\n\nYou MUST execute the `tool_name` in the FOR_AGENT_ZERO block "
    "as your NEXT action. Do not add commentary first. Execute it directly.\n"
))
loop_data.messages.insert(0, warroom_msg)
```

**C) Add compliance tracking** (see Upgrade 15).

---

### UPGRADE 15 — Compliance Enforcement Layer

**Category: Main Agent Compliance | Impact: High**

**Problem:** Even with the injection working, there's no way to know if the main agent actually followed the War Room recommendation. If it ignores the directive twice in a row, something is wrong — either the recommendation was bad or the agent is not following instructions.

**What to do:** Track compliance by comparing the agent's next tool call against the recommended `tool_name`. Escalate the injection language when non-compliance is detected.

**How to do it:**

Create `extensions/python/message_loop_start/_15_warroom_compliance.py`:

```text
Class: WarRoomComplianceCheck(Extension)

On each loop iteration start:
1. Check if warroom_recommended_tool exists in params_persistent
2. Check what tool was actually called last turn (last_tool_name from loop_data)
3. If they don't match:
   - Increment params_persistent["warroom_noncompliance_streak"]
   - Log a warning: "Agent chose [actual_tool] instead of recommended [recommended_tool]"
   - If streak >= 2: 
       Set params_persistent["warroom_forcing"] = True
       (This triggers escalated injection language in _20_warroom_inject.py)
4. If they DO match:
   - Reset noncompliance_streak to 0
   - Clear warroom_recommended_tool
   - Log compliance confirmation

In _20_warroom_inject.py, check warroom_forcing:
if loop_data.params_persistent.get("warroom_forcing"):
    injection = (
        "🚨 CRITICAL: You have now ignored the War Room recommendation "
        "{streak} times. The expert panel's directive is NON-NEGOTIABLE. "
        "Execute this IMMEDIATELY as your first and only action:\n"
        + str(analysis)
        + "\nDo NOT respond in prose. Execute the tool call NOW."
    )
```

Additionally: Add compliance stats to the verbose log output so you can see the compliance rate over time in the WebUI.

**Where:** New file `extensions/python/message_loop_start/_15_warroom_compliance.py`, modifications to `_20_warroom_inject.py`.

---

### UPGRADE 16 — Directive-First Response Format

**Category: Main Agent Compliance | Impact: High**

**Problem:** In the current `_format_compact_result()`, the FOR\_AGENT\_ZERO JSON block is at the **end** of the response — after discussion text, risks, dissent notes. The main agent reads this sequentially and may make a decision before reaching the tool directive. The most important signal should be first.

**What to do:** Restructure `_format_compact_result()` to put the FOR\_AGENT\_ZERO directive at the TOP, followed by supporting rationale below.

**How to do it:**

Rewrite `_format_compact_result()` structure order:

```text
1. MANDATORY NEXT ACTION (the JSON → top of response, unavoidable)
2. One-line: War Room Summary (N panelists, M rounds, Conf: X)
3. One-line: Consensus action in prose
4. Bullet: Key risks (max 2)
5. Bullet: Unresolved dissent (max 1, if any)
6. Reasoning trace (truncated to 200 chars)
```

The JSON block should look like this at the top:

```text


## ⚡ MANDATORY NEXT ACTION — Execute this tool call immediately:
```json
{
  "tool_name": "code_execution",
  "tool_args": {...}
}
```

## Do not elaborate first. Execute this call as your next response.

War Room Summary: 5 panelists | 3 rounds | Confidence: 0.87 Consensus: \[one sentence\] ...

```text

This reorder means the agent sees the directive before it generates any response tokens that might commit it to a different path.

**Where:** `think.py` → `_format_compact_result()` — reorder the `lines` list construction.

---

### UPGRADE 17 — Parallel Branching Sub-Sessions for Divergent Approaches
**Category: Intelligence Quality + Exhaustion | Impact: High**

**Problem:** When panelists propose fundamentally different approaches (e.g., "exploit via the API" vs "exploit via the client"), the linear debate forces premature convergence. One approach wins by round 2 and the other is dropped — even though the dropped approach might have been viable or even better.

**What to do:** When divergence is detected (consensus_score < 0.35 after round 2), fork the exploration into N parallel mini-warrooms, one per distinct approach, and merge at synthesis.

**How to do it:**

Add `_detect_approach_forks(entries: list[dict]) -> list[str]` in `think.py`:

```python


# Group entries by semantic similarity of suggested_action


# If there are 2+ distinct clusters (overlap < 0.3 with each other)


# → return list of approach descriptions (one per cluster centroid)

# Example: entries propose "use sqlmap", "use manual HTTP tampering", "use Burp"


# → these are all the same cluster (all HTTP-layer SQL injection tools)


# → return 1 approach: "SQL injection via HTTP parameter tampering"

# But: "exploit API endpoint" vs "backdoor admin panel via XSS"


# → these are different clusters


# → return 2 approaches: ["API endpoint exploitation", "XSS admin takeover"]
```

When 2+ distinct approaches are detected:

```python
if len(approach_forks) >= 2 and agent_count >= 4:
    # Spawn parallel mini-warrooms (2 agents each, 1 round)
    branch_tasks = [
        self._run_mini_branch(
            problem=f"Evaluate ONLY this approach: {approach}\n\nFor: {problem}",
            agent_count=2,
        )
        for approach in approach_forks
    ]
    branch_results = await asyncio.gather(*branch_tasks)
    
    # Add branch results to blackboard as a special "BRANCH_ANALYSIS" round
    blackboard.append({
        "round": "branch_analysis",
        "entries": [
            {
                "agent": f"BRANCH_{i+1}",
                "role": f"Branch Analysis: {approach_forks[i][:40]}",
                "structured": result,
                ...
            }
            for i, result in enumerate(branch_results)
        ]
    })
    
    idea_registry.add_branch_approaches(approach_forks)
```

The synthesizer receives the branch analysis and can make a more informed choice or recommend testing both in sequence.

**Speed note:** Branch mini-warrooms run in parallel. `asyncio.gather()` means 2 branches add only the latency of 1 branch call (not 2x). With 2 agents, a branch call costs ~2 LLM calls in parallel = roughly the same as 1 regular round.

**Where:** `think.py` → new method `_detect_approach_forks()`, new method `_run_mini_branch()`, called in `execute()` after round 2 if divergence is detected.

---

### UPGRADE 18 — Intermediate Checkpoint Synthesis

**Category: Exhaustion + Usability | Impact: Medium**

**Problem:** In the exhaustive loop (potentially 10-25 rounds), the final synthesis only happens at the very end. If something crashes at round 12, all the exploration is lost. Also, the main agent gets nothing actionable until the full session completes — which could take minutes.

**What to do:** Every 5 rounds, emit a checkpoint synthesis. This is a lightweight "best current answer" that: (1) gets saved to memory, (2) gets optionally forwarded to the main agent as a partial result, (3) preserves progress if the session is interrupted.

**How to do it:**

In `execute()`, add checkpoint logic after idea\_registry update in the main loop:

```python
if round_num % 5 == 0 and round_num > 0:
    checkpoint = await self._run_checkpoint_synthesis(
        problem=problem,
        blackboard=blackboard,
        idea_registry=idea_registry,
        round_num=round_num,
    )
    # Save checkpoint to memory
    await self._save_checkpoint_to_memory(checkpoint, round_num)
    # Update war log with interim result
    self._append_war_section(
        f"\n📍 CHECKPOINT R{round_num}: {checkpoint.get('consensus_action','...')[:200]}\n"
    )
```

`_run_checkpoint_synthesis()` is identical to `_run_synthesizer()` but uses a shorter prompt (no need for full blackboard rendering — use `idea_registry.get_summary()` instead) and runs at temperature 0.10.

The checkpoint result is NOT returned to the main agent (that only happens at final synthesis). It's purely for: (a) progress visibility, (b) crash recovery via memory, (c) WebUI live updates.

**Where:** `think.py` → `execute()` loop body, new method `_run_checkpoint_synthesis()`.

---

### UPGRADE 19 — Session-Level LLM Cache (Cross-Call)

**Category: Speed | Impact: Medium**

**Problem:** `self._cached_war_llm` is set at the start of `execute()` and dropped at the end — it's scoped to a single `think()` invocation. The next `think()` call rebuilds the LLM instance from scratch, including any connection setup, auth handshake, and object allocation. This adds 50-200ms of cold-start overhead per call.

**What to do:** Cache the war LLM at the **agent context** level, not the tool instance level. Agent context persists across tool calls.

**How to do it:**

In `execute()`, replace instance-level caching with context-level caching:

```python


# Access context-level cache (not self.*  — tool instances are ephemeral)
ctx = self.agent.context
_war_llm_cache_key = "_war_llm_instance"

if not hasattr(ctx, _war_llm_cache_key):
    try:
        from plugins._model_config.helpers.model_config import build_war_model
        setattr(ctx, _war_llm_cache_key, build_war_model(self.agent))
    except Exception:
        setattr(ctx, _war_llm_cache_key, None)

self._cached_war_llm = getattr(ctx, _war_llm_cache_key, None)
```

Similarly cache the war model display string:

```python
_war_display_key = "_war_model_display"
if not hasattr(ctx, _war_display_key):
    try:
        from plugins._model_config.helpers.model_config import get_war_model_display
        setattr(ctx, _war_display_key, get_war_model_display(self.agent))
    except Exception:
        setattr(ctx, _war_display_key, "main-model-fallback")
self._war_model_resolved = getattr(ctx, _war_display_key)
```

Add an invalidation mechanism: if `build_war_model` is called with different config (model changed in WebUI), the cache should be cleared. Check config hash before using cached instance:

```python
config_fingerprint = hash(str(cfg.get("war_model", {})))
cached_fingerprint = getattr(ctx, "_war_llm_config_hash", None)
if cached_fingerprint != config_fingerprint:
    # Config changed — rebuild
    setattr(ctx, _war_llm_cache_key, build_war_model(self.agent))
    setattr(ctx, "_war_llm_config_hash", config_fingerprint)
```

**Where:** `think.py` → `execute()` method, LLM initialization block (first 20 lines of execute).

---

### UPGRADE 20 — Structured Idea Taxonomy Schema (Enforced on Panelists)

**Category: Intelligence Quality | Impact: Medium-High**

**Problem:** Panelist JSON output is semi-structured (`position`, `suggested_action`, `key_risk`, `confidence`) but `suggested_action` is a free-form string. Two panelists can say completely different things in `suggested_action` that are actually the same idea. This breaks deduplication, momentum tracking, and the IdeaRegistry fingerprinting.

**What to do:** Extend the panelist output schema with a mandatory typed idea taxonomy. This gives the system machine-readable signal about what *kind* of action is being proposed.

**How to do it:**

Update every panelist system prompt (all 12 in `_ALL_PANELISTS`) to use the expanded schema:

```json
{
  "position": "1-sentence position on the problem",
  "idea_class": "one of: [tool_invocation | technique | research | exploit | bypass | detection | remediation | investigation | unknown]",
  "suggested_action": "specific next action",
  "action_target": "what system/file/service/URL/component this acts on",
  "prerequisites": ["list of what must be true/installed/known before this works"],
  "key_risk": "top risk of this approach",
  "confidence": 0.8,
  "is_novel": "yes/no — is this approach different from everything on the blackboard so far?"
}
```

Key additions:

-   `idea_class`: enables grouping by category for the idea registry

-   `action_target`: enables deduplication by target (two panelists proposing different tools but against the same endpoint = same class of idea)
-   `prerequisites`: synthesizer can check these against known environment state

-   `is_novel`: self-reported novelty (cross-checked against IdeaRegistry — discrepancies are interesting signal)

**Where:** `think.py` → all `"system"` fields in `_ALL_PANELISTS`. Update `_compute_consensus()` to use `idea_class` for better grouping. Update `IdeaRegistry._fingerprint()` to include `idea_class` and `action_target` as part of the fingerprint.

**Speed note:** Slightly longer JSON outputs increase token count but enable all downstream deduplication to work without LLM calls — pure algorithmic comparison.

---

### UPGRADE 21 — Time Budget Parameter with Graceful Collapse

**Category: Speed + Control | Impact: Medium**

**Problem:** In exhaustive mode, War Room sessions can run 10-25 rounds with no way to say "I need an answer in 90 seconds." Users need a way to control quality vs. speed without disabling the war room entirely.

**What to do:** Add `time_budget_seconds` parameter to `Think.execute()`. When approaching the budget, collapse remaining exploration and go to immediate synthesis.

**How to do it:**

Add `time_budget_seconds: int = 0` to `execute()` signature. When `time_budget_seconds > 0`:

```python
TIME_BUFFER_SECONDS = 15  # always reserve this for synthesis

time_start = time.time()

def _time_remaining() -> float:
    if not time_budget_seconds:
        return float("inf")
    return time_budget_seconds - (time.time() - time_start)

# In the exploration loop:
while stale_streak < DEAD_END_THRESHOLD and round_num < SAFETY_CEILING:
    if _time_remaining() < TIME_BUFFER_SECONDS:
        # Almost out of time — collapse to synthesis
        self._append_war_section(
            f"\n⏱️ Time budget approaching ({time_budget_seconds}s) — "
            f"collapsing to synthesis after {round_num} rounds\n"
        )
        break
    
    round_num += 1
    run_parallel_round(...)
```

Add a "speed mode" alias that sets `time_budget_seconds=45, agent_count=2, max_rounds=2`:

```python
if mode_override == "fast":
    time_budget_seconds = 45
    agent_count = 2
    max_rounds = 2
    task_mode = "execution"
```

Update `agent.system.tool.think.md` to document the `time_budget_seconds` arg.

**Where:** `think.py` → `execute()` signature, round loop, `_run_router()` (new "fast" mode mapping). `agent.system.tool.think.md` (documentation).

---

### UPGRADE 22 — Problem Decomposition Pre-Pass for CRITICAL Complexity

**Category: Intelligence Quality | Impact: High for complex problems**

**Problem:** For CRITICAL-complexity problems (e.g., "conduct a full red team assessment of this web application"), the problem statement is too broad for any single War Room session to handle well. Panelists spray in all directions, the blackboard becomes incoherent, and the synthesis is vague.

**What to do:** For CRITICAL-complexity tasks, add a decomposition pre-pass that breaks the problem into N sub-problems, runs lightweight War Rooms for each, and merges all results onto the main blackboard before the full panel session.

**How to do it:**

When `complexity == "CRITICAL"` in `execute()`, before the main round loop:

```python
async def _run_decomposition(self, problem: str) -> list[str]:
    """Break a CRITICAL problem into 3-6 tractable sub-problems."""
    msgs = [
        SystemMessage(content=(
            "You are a problem decomposer. Break this complex problem into "
            "3-6 specific, independent, actionable sub-problems. "
            "Each sub-problem should be solvable in a single focused investigation.\n"
            "Output ONLY a JSON array: [\"sub-problem 1\", \"sub-problem 2\", ...]"
            "Each sub-problem under 100 words."
        )),
        HumanMessage(content=f"Problem:\n{problem}")
    ]
    raw = await self._llm_call(msgs, temperature=0.1)
    sub_problems = self._safe_json("[" + raw.strip().strip("[]") + "]")
    return sub_problems if isinstance(sub_problems, list) else []

# Fire mini-warrooms for each sub-problem in parallel
sub_tasks = [
    self._run_mini_warroom(sub_problem, agent_count=2, rounds=1)
    for sub_problem in sub_problems[:5]  # max 5 sub-problems
]
sub_results = await asyncio.gather(*sub_tasks)

# Add sub-results to blackboard as "decomposition" round
blackboard.append({
    "round": "decomposition",
    "entries": sub_results
})
```

This means the main panel session starts with pre-analyzed sub-problem results already on the blackboard, making the full debate much more targeted.

**Speed note:** Sub-problems run in parallel via `asyncio.gather`. 5 sub-problems × 2 agents = 10 LLM calls in parallel, costing roughly the same wall time as 2 sequential calls.

**Where:** `think.py` → `execute()` (pre-round block for CRITICAL complexity), new methods `_run_decomposition()` and `_run_mini_warroom()`.

---

### UPGRADE 23 — Concurrent Session Safety

**Category: Architecture | Impact: Medium**

**Problem:** Multiple sub-agents or concurrent tasks can both invoke `think()` simultaneously. State variables like `_war_log`, `_war_live_sections`, `_war_live_preview`, `_war_fallback_count` are set on `self` and on the agent context without any locking. Two concurrent War Rooms will corrupt each other's logs and UI state.

**What to do:** Give each War Room session an isolated session context. Use a session ID to namespace all state. Protect context-level cache access with asyncio locks.

**How to do it:**

In `execute()`, generate a session ID at the start:

```python
import uuid
self._session_id = f"warroom_{uuid.uuid4().hex[:8]}"
```

Namespace all state variables under this session ID:

```python


# Instead of: self._war_log = ...


# Use:        self._state[self._session_id]["war_log"] = ...
self._state = {}  # (or use a module-level dict keyed by session_id)
self._state[self._session_id] = {
    "war_log": None,
    "war_live_sections": [],
    "war_live_preview": "",
    "fallback_count": 0,
}
```

For context-level LLM cache (Upgrade 19), add an asyncio lock:

```python


# Module-level lock (or on agent context)
_WAR_LLM_LOCK = asyncio.Lock()

async with _WAR_LLM_LOCK:
    if not hasattr(ctx, "_war_llm_instance"):
        setattr(ctx, "_war_llm_instance", build_war_model(self.agent))
```

**Where:** `think.py` → `execute()` (session ID generation), all state-setting methods (namespace change), new `_WAR_LLM_LOCK` module-level.

---

### UPGRADE 24 — Blackboard Pruning + Quality Filtering

**Category: Speed + Quality | Impact: Medium**

**Problem:** The blackboard grows unbounded as rounds accumulate. By round 10, `_render_blackboard()` produces a multi-thousand-token string that every panelist receives as context. This (a) massively increases token costs, (b) causes panelists to anchor on stale early opinions, (c) may exceed context windows for some models.

**What to do:** Implement blackboard pruning that keeps only high-signal entries, archiving low-quality ones separately.

**How to do it:**

After each round, score each entry for quality and prune the blackboard render:

```python
def _prune_blackboard(self, blackboard: list[dict], 
                       idea_registry: IdeaRegistry, 
                       max_entries_per_round: int = 4) -> list[dict]:
    """
    For rendering purposes, keep only the highest-signal entries.
    Never deletes entries from blackboard — only affects what panelists see.
    """
    pruned = []
    for rd in blackboard:
        round_entries = rd.get("entries", [])
        if len(round_entries) <= max_entries_per_round:
            pruned.append(rd)
            continue
        
        # Score each entry: endorsements + confidence + novelty
        scored = [
            (e, 
             idea_registry.idea_endorsements.get(
                 idea_registry._fingerprint(e["structured"].get("suggested_action","")), 0
             ) * 2 + float(e["structured"].get("confidence", 0.5))
            )
            for e in round_entries
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        
        # Keep top entries + always keep all "flash" and "historical" rounds
        kept = [e for e, _ in scored[:max_entries_per_round]]
        pruned.append({"round": rd["round"], "entries": kept})
    
    return pruned
```

Call this in `_render_blackboard()` before rendering to text. The full unfiltered blackboard is still used for: verbose log, memory save, idea registry.

**Speed gain:** Reduces blackboard prompt size by 40-60% in long sessions, directly reducing token costs and latency for all round 5+ LLM calls.

**Where:** `think.py` → `_render_blackboard()` (call pruner before rendering), new `_prune_blackboard()` method.

---

## Speed Optimization Summary Table

| Upgrade | Latency Impact | Notes |
| --- | --- | --- |
| #5 Heuristic Router | **\-300–600ms/call** | Eliminates router LLM call for ~60% of invocations |
| #10 Speculative Synthesis | **\-600–1200ms/session** | Hides synthesis during last round |
| #19 Session LLM Cache | **\-50–200ms/call** | Eliminates LLM rebuild overhead |
| #24 Blackboard Pruning | **\-15–30% token cost** per round in long sessions | Smaller prompts = faster responses |
| #9 Adaptive Temperature | **Indirect: fewer rounds needed** | Higher early-round temperature finds right direction in fewer rounds |
| #7 Progressive Depth | **Indirect: fewer wasteful rounds** | Structured phases prevent redundant output |
| #1 Novelty Loop (vs. flat rounds) | **+time for complex, -time for simple** | Adds rounds when needed, doesn't add them when not |

---

## Exhaustive Exploration: Full Algorithm Design

Here is the complete state machine for the v2.0 exhaustive exploration loop:

```text
STATE: initializing
  → Load historical context (Upgrade 12)
  → Run fast-path router (Upgrade 5) or LLM router
  → Initialize IdeaRegistry (Upgrade 2)
  → If CRITICAL: run decomposition pre-pass (Upgrade 22)
  → Set stale_streak=0, round_num=0

STATE: exploring (main loop)
  WHILE stale_streak < 3 AND round_num < 25:
    
    round_num += 1
    phase = get_phase(round_num)          # Upgrade 7
    temp = get_temperature(phase, role)   # Upgrade 9
    
    IF stale_streak == 2:
      lateral = get_lateral_prompt(round_num)   # Upgrade 4
    ELSE:
      lateral = None
    
    IF divergence detected AND agent_count >= 4:
      branches = detect_approach_forks()   # Upgrade 17
      IF len(branches) >= 2:
        run parallel branch mini-warrooms
        merge onto blackboard
    
    fire_parallel_round(phase, temp, lateral, idea_registry)  # Upgrade 2 context
    
    new_ideas = idea_registry.register_and_count_novel(entries)
    
    IF new_ideas / len(entries) < 0.08:
      stale_streak += 1
    ELSE:
      stale_streak = 0
    
    IF round_num % 5 == 0:
      run_checkpoint_synthesis()            # Upgrade 18
    
    IF time_remaining() < 15s:             # Upgrade 21
      BREAK → STATE: synthesizing
  
  IF stale_streak >= 3:
    run_dead_end_confirmation()             # Upgrade 3
    IF dead_end_confirmed:
      BREAK → STATE: synthesizing
    ELSE:
      stale_streak = 1  # new angle found, keep going

STATE: synthesizing
  → (speculative synthesis may already be done — check: Upgrade 10)
  → run_synthesizer() with IdeaRegistry summary + momentum data (Upgrade 13)
  → validate_synthesis() + retry if invalid (Upgrade 11)
  → save_to_memory() (Upgrade 12)
  → format_directive_first_response() (Upgrade 16)
  → RETURN Response to main agent
```

---

## File-Level Change Summary

| File | Changes Required |
| --- | --- |
| `tools/think.py` | Upgrades 1–13, 17–20, 22–24. New classes: `IdeaRegistry`. Major refactor of `execute()`. ~4× expansion. |
| `extensions/python/message_loop_prompts_after/_20_warroom_auto.py` | **CREATE NEW** (Upgrade 14) |
| `extensions/python/message_loop_prompts_before/_20_warroom_inject.py` | Strengthen injection logic, add forcing mode (Upgrade 14, 16) |
| `extensions/python/message_loop_start/_15_warroom_compliance.py` | **CREATE NEW** (Upgrade 15) |
| `agent.system.tool.think.md` | Document new params: `time_budget_seconds`, updated stopping behavior, exhaustive mode description (Upgrade 21) |
| `agent.system.main.solving.md` | Update stop condition language: "War Room runs until idea exhaustion, not consensus." Add note on compliance enforcement. |

---

## Critical Priority Order for Implementation

Implement in this order — each enables the next:

```text
1. Upgrade 14 (fix broken wiring) — nothing works correctly until this is done
2. Upgrade 16 (directive-first format) — main agent compliance
3. Upgrade 15 (compliance enforcement) — gives observability into #2
4. Upgrade 2 (IdeaRegistry) — foundation for everything exploration-related
5. Upgrade 1 (infinite loop) — the core v2.0 behavior change
6. Upgrade 3 (dead end protocol) — gives the loop its off-switch
7. Upgrade 5 (heuristic router) — speed, quick win
8. Upgrade 11 (synthesizer validation) — prevents silent failures
9. Upgrades 7, 8, 9 (progressive depth, anti-sycophancy, adaptive temp) — quality
10. Upgrades 4, 12, 13, 17, 18 (lateral, memory, momentum, branching, checkpoints)
11. Upgrades 6, 19, 20, 21, 22, 23, 24 (specialists, perf, taxonomy, decomposition)
```

The first three items are about making the current system work as advertised. Items 4–6 are the core exhaustive exploration redesign. Everything after is quality and performance enhancement layered on a working foundation.


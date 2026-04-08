# Multi-Agent "War Room" Architecture

## The Core Problem with Sequential

Your current Expert Panel (from the logs) works like this:

```text
Round 1: ALPHA â†’ BETA â†’ GAMMA â†’ DELTA (parallel but isolated)
Debate:  Everyone reads Round 1, responds (still one batch)
Synth:   DELTA summarizes
```

This is **parallel-then-sequential**â€”agents don't truly *react* to each other mid-thought. It took ~63 seconds in your logs (01:12:24 â†’ 01:13:25).

---

## The New Architecture: Blackboard + Micro-Rounds

### Core Concept

Imagine a shared whiteboard in a war room. Everyone writes short notes simultaneously, reads what others wrote, reacts, writes again. Many fast micro-rounds instead of few long rounds.

```text
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                   SHARED BLACKBOARD                   â”‚
â”‚                                                       â”‚
â”‚  [STRATEGIST]: "Attack surface is the WS handshake"  â”‚
â”‚  [CHALLENGER]: "No, the message layer is weaker"     â”‚
â”‚  [EXECUTOR]:   "I can test both in parallel via..."  â”‚
â”‚  [RESEARCHER]: "CVE-2025-XXXX is relevant here"      â”‚
â”‚                                                       â”‚
â”‚  â”€â”€ Round 2 â”€â”€                                        â”‚
â”‚  [CHALLENGER]: "Good point @RESEARCHER, but..."      â”‚
â”‚  [STRATEGIST]: "Adjusting: prioritize message layer" â”‚
â”‚  [EXECUTOR]:   "Revised plan: step 1..."             â”‚
â”‚                                                       â”‚
â”‚  â”€â”€ Round 3 (only dissenters) â”€â”€                     â”‚
â”‚  [CHALLENGER]: "Final objection on..."               â”‚
â”‚  [STRATEGIST]: "Acknowledged, mitigated by..."       â”‚
â”‚                                                       â”‚
â”‚  â”€â”€ SYNTHESIS â”€â”€                                      â”‚
â”‚  [SYNTHESIZER]: "Consensus: do X, then Y, risk Z"   â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

---

## Detailed Workflow

### Step 0: Complexity Router (< 1 second)

Before any multi-agent call, a **Utility Model Call** to classifies the task:

```text
Input:  The current prompt/context (compressed)
Output: { complexity: "HIGH" | "MEDIUM" | "LOW", 
          agent_count: 6 | 3 | 1,
          rounds: 4 | 2 | 0,
          specialists_needed: ["researcher", "critic"] }
```

**How it decides:**

-   **HIGH** (4-6 agents, 3-4 micro-rounds): Novel tasks, first encounter with a problem, ambiguous situations, security-critical decisions, planning phases

-   **MEDIUM** (2-3 agents, 2 micro-rounds): Tool result analysis where result is ambiguous, moderate complexity follow-up
-   **LOW** (1 agent, 0 rounds): Straightforward tool execution, clear next step, simple data extraction

This single call uses a **small/fast model - Utility Model** (like GPT-4o-mini) with a structured output schema. It costs almost nothing and saves you from running 6 agents on a trivial task.

---

### Step 1: Parallel Pitch â€” All Agents Fire Simultaneously (8-12 sec)

Every selected agent gets the SAME prompt package at the SAME time via **concurrent async API calls**:

```text
Each agent receives:
â”œâ”€â”€ System prompt: Their role persona (50-100 words max)
â”œâ”€â”€ Shared context: The task/problem
â”œâ”€â”€ Blackboard state: Empty (first round)
â”œâ”€â”€ Instruction: "Give your initial position in 80-100 words MAX. 
â”‚                 Be specific and actionable. Name concrete tools, 
â”‚                 techniques, or steps."
â””â”€â”€ Format: Structured JSON { position, confidence, key_risk, suggested_action }
```

**Why short responses matter:**

-   100 words â‰ˆ ~130 tokens â‰ˆ 2-3 seconds generation time

-   Allows 4-5 rounds in 60 seconds instead of 2 long rounds
-   Forces agents to be *precise*, not verbose

-   More rounds = more cross-pollination = better "group discussion" feel

**Agents fire truly in parallel** â€” 6 API calls at once. They all return in ~3-8 seconds (limited by the slowest one).

---

### Step 2: Blackboard Write + Divergence Detection (< 0.5 sec)

This is a **local computation step + Utility Model**, call:

```text
1. Collect all Round 1 responses
2. Write them to the shared blackboard data structure
3. Run divergence detection (embedding similarity):
   - Extract each agent's "suggested_action" and "position"
   - Compare pairwise â€” if agents disagree, flag them
   - Calculate consensus_score (0-1)
   
4. If consensus_score > 0.85 â†’ Skip to Synthesis (everyone agrees)
   If consensus_score > 0.60 â†’ 1 more round with all agents
   If consensus_score < 0.60 â†’ 2 more rounds, bring in extra specialist
```

**This is the intelligence layer** â€” it prevents wasting time when agents already agree, and escalates when there's genuine disagreement.

---

### Step 3: Reactive Micro-Round â€” Cross-Pollination (8-12 sec)

All agents fire again in parallel, but NOW the prompt includes:

```text
Each agent receives:
â”œâ”€â”€ System prompt: Same role persona
â”œâ”€â”€ Shared context: Same task
â”œâ”€â”€ Blackboard state: ALL of Round 1's outputs (the magic ingredient)
â”œâ”€â”€ Instruction: "You've read everyone's positions. In 60-80 words:
â”‚                 1. Respond to ONE specific point from another agent
â”‚                 2. Refine OR defend your position  
â”‚                 3. Identify any risk the group is missing"
â””â”€â”€ Format: { responding_to, refined_position, group_risk }
```

**Why this feels like a real discussion:**

-   Agents explicitly reference each other ("@CHALLENGER raises a good point about X, but...")

-   Positions *evolve* based on others' input
-   Risks snowball â€” one agent's concern triggers another's insight

---

### Step 4: Flash Debate â€” Dissenters Only (5-8 sec, often skipped)

**Only runs if divergence is still detected after Step 3.**

```text
1. Local computation: Re-run divergence detection on Round 2
2. Identify agents who STILL disagree with majority
3. Only those agents + one "majority representative" fire:
   
   Dissenter prompt: "The group consensus is [X]. You disagree because [Y]. 
                      Final 40-word defense. Be specific about consequences 
                      of ignoring your concern."
   
   Majority prompt:  "Dissent point: [Y]. In 40 words, explain why the 
                      group approach handles this, or acknowledge it as a risk."
```

This is typically 2 agents, very short responses, ~5 seconds. Often the divergence detection finds consensus already reached and **skips this entirely**.

---

### Step 5: Synthesis (5-8 sec)

**One API call** to the Synthesizer agent:

```text
Input:
â”œâ”€â”€ Full blackboard (all rounds)
â”œâ”€â”€ Divergence data (who agreed, who dissented, on what)
â”œâ”€â”€ Original task context

Output (structured):
â”œâ”€â”€ consensus_action: The agreed-upon next step (tool call, plan, etc.)
â”œâ”€â”€ confidence: 0-1
â”œâ”€â”€ key_risks: [list of unresolved concerns]
â”œâ”€â”€ dissent_notes: Any minority opinions worth preserving
â”œâ”€â”€ reasoning_trace: Brief chain of how consensus emerged
â””â”€â”€ for_agent_zero: The actual formatted output (tool call JSON, etc.)
```

---

## Total Timeline

```text
Step 0: Router .............. 0.5-1 sec
Step 1: Parallel Pitch ...... 8-12 sec (all agents, parallel)
Step 2: Divergence Check .... 0.1-0.5 sec (local, no API)
Step 3: Reactive Round ...... 8-12 sec (all agents, parallel)
Step 2b: Divergence Check ... 0.1-0.5 sec (local, no API)
Step 4: Flash Debate ........ 0-8 sec (often skipped, 2 agents max)
Step 5: Synthesis ........... 5-8 sec (1 agent)
                              â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                  TOTAL:      22-42 seconds typical
                  MAX:        ~60 seconds worst case
```

Compare to your current system from logs: ~63 seconds. This is **faster AND more interactive**.

---

## Integration with Agent-Zero

### Current Agent-Zero Flow:

```text
User prompt 
  â†’ Context building (memory + knowledge)  
    â†’ AI API call  
      â†’ Tool selection response  
        â†’ Tool execution  
          â†’ Tool result back to AI API  
            â†’ Next tool or final response
```

### New Flow â€” Every API Call Becomes Smart:

```text
User prompt
  â†’ Context building (memory + knowledge)
    â†’ COMPLEXITY ROUTER (fast, ~1 sec)
      â”œâ”€â”€ HIGH complexity? â†’ Full War Room (4-6 agents, 3-4 rounds)
      â”œâ”€â”€ MED complexity?  â†’ Mini War Room (2-3 agents, 2 rounds)  
      â””â”€â”€ LOW complexity?  â†’ Single agent (normal call)
    â†’ Consensus output â†’ Tool selection
      â†’ Tool execution
        â†’ Tool result  
          â†’ RESULT ROUTER (fast, ~1 sec) 
            â”œâ”€â”€ Ambiguous result?  â†’ Mini War Room (2-3 agents)
            â”œâ”€â”€ Error/unexpected?  â†’ Full War Room (4-6 agents)
            â””â”€â”€ Clear result?      â†’ Single agent continues
          â†’ Next action...
```

### What the Routers Evaluate:

**Complexity Router (pre-action):**

```text
Factors:
â”œâ”€â”€ Is this the FIRST interaction? (Yes â†’ HIGH, needs planning)
â”œâ”€â”€ Is this a security/critical decision? (Yes â†’ HIGH)
â”œâ”€â”€ Is the task ambiguous? (Yes â†’ HIGH)
â”œâ”€â”€ Is this a follow-up to an already-planned step? (Yes â†’ LOW)
â”œâ”€â”€ Has the expert panel already deliberated on this? (Yes â†’ LOW)
â”œâ”€â”€ How many tools could this require? (Many â†’ MEDIUM+)
â””â”€â”€ Is this a simple data retrieval? (Yes â†’ LOW)
```

**Result Router (post-tool):**

```text
Factors:
â”œâ”€â”€ Did the tool return an error? (Yes â†’ MEDIUM, need to diagnose)
â”œâ”€â”€ Is the result ambiguous/unexpected? (Yes â†’ MEDIUM+)
â”œâ”€â”€ Does the result require interpretation? (Yes â†’ MEDIUM)
â”œâ”€â”€ Is this a security finding that needs validation? (Yes â†’ HIGH)
â”œâ”€â”€ Is this just raw data to pass forward? (Yes â†’ LOW)
â””â”€â”€ Does this change the original plan significantly? (Yes â†’ HIGH)
```

---

## Agent-Zero Phase-Specific Integration

Based on your logs, here's exactly how each phase maps:

### Phase 1: User Message Arrives â†’ Planning

```text
COMPLEXITY: Always HIGH for initial task
AGENTS: 6 (Strategist + Challenger + Executor + Researcher + Critic + Synthesizer)
ROUNDS: 3-4
PURPOSE: Create the master plan, identify attack surface, prioritize
OUTPUT: Structured execution plan with ordered steps
```

### Phase 2: Research Tool Calls (search\_engine)

```text
COMPLEXITY: Typically LOW-MEDIUM
AGENTS: 1-2 (just the Executor, maybe Researcher)
ROUNDS: 1
PURPOSE: Search queries are usually straightforward
OUTPUT: Search query to execute
```

### Phase 3: Research Results Analysis

```text
COMPLEXITY: MEDIUM (need to evaluate quality of findings)
AGENTS: 3 (Researcher + Strategist + Executor)
ROUNDS: 2
PURPOSE: Evaluate if research is sufficient, identify gaps
OUTPUT: Continue researching or move to testing
```

### Phase 4: Tool Execution (Burp MCP, code\_execution, etc.)

```text
COMPLEXITY: Varies â€” Router decides
AGENTS: 1-3
ROUNDS: 1-2
PURPOSE: Determine exact tool parameters and payloads
OUTPUT: Precise tool call with arguments
```

### Phase 5: Tool Result Analysis

```text
COMPLEXITY: MEDIUM-HIGH (especially for security findings)
AGENTS: 2-4 (Executor + Critic + maybe Challenger)
ROUNDS: 2
PURPOSE: Is this a real finding? False positive? Need more testing?
OUTPUT: Decision + next action
```

### Phase 6: Final Report

```text
COMPLEXITY: HIGH (need quality, accuracy, completeness)
AGENTS: 4-5
ROUNDS: 3
PURPOSE: Ensure report is accurate, well-structured, no gaps
OUTPUT: Final report
```

---

## The Agent Personas (Compact)

Each agent gets a TINY system prompt (~100 words) to keep tokens low:

```text
STRATEGIST:  "You are the strategic planner. Focus on: attack surface mapping,
             prioritization, resource allocation, risk/reward tradeoffs. 
             Always think 3 steps ahead. Be decisive."

CHALLENGER:  "You are the devil's advocate. Your job: find flaws in every plan,
             identify blind spots, stress-test assumptions. If everyone agrees,
             find what they're missing. Be constructive â€” always suggest fixes."

EXECUTOR:    "You are the implementer. Focus on: exact commands, tool parameters,
             concrete steps, practical constraints. No hand-waving â€” if you 
             can't specify the exact action, say so."

RESEARCHER:  "You are the knowledge specialist. Focus on: relevant CVEs, latest
             techniques, prior art, documentation references. Always cite 
             specific sources or techniques by name."

CRITIC:      "You are the quality reviewer. Focus on: edge cases, error handling,
             false positive/negative analysis, completeness checking. If a test
             can fail silently, flag it."

SYNTHESIZER: "You are the consensus builder. Identify agreements, resolve 
             disagreements by weighing evidence, produce clear actionable output.
             Your output IS the group's decision."
```

---

## Making It Real-Time (The Streaming Trick)

To make agents feel like they're talking in real-time rather than batch rounds:

### Approach: Staggered Streaming with Early Injection

```text
1. Fire all agents in parallel with streaming enabled
2. As EACH agent's response streams in token-by-token:
   - Display it in real-time (if you have a UI)
   - When Agent A finishes (while B,C,D still streaming):
     â†’ IMMEDIATELY start Agent A's Round 2, injecting whatever 
       partial outputs exist from B,C,D
3. This means faster agents "react" to slower agents' partial thoughts
4. Creates a genuinely overlapping, non-sequential discussion
```

```text
Timeline visualization:

Agent A: [====PITCH====]â†’[==REACT=====]â†’[DONE]
Agent B: [======PITCH=======]â†’[===REACT===]â†’[DONE]
Agent C: [====PITCH=====]â†’[====REACT======]â†’[DONE]
Agent D: [========PITCH=========]â†’[=REACT=]â†’[DONE]
                                              â†“
                                         [SYNTHESIS]

Instead of:
Round 1: [A,B,C,D all finish]â”€â”€waitâ”€â”€â†’ Round 2: [A,B,C,D all finish]â”€â”€waitâ”€â”€â†’ Synth

The staggered approach means Round 2 begins for fast agents 
BEFORE Round 1 finishes for slow agents.
```

This saves 5-10 seconds on a 4-agent setup.

---

## Dynamic Agent Count â€” The Intelligence Layer

### How the system decides agent count at each step:

```text
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚              DECISION MATRIX                      â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ Situation       â”‚Agents â”‚ Rounds â”‚ Why           â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ Initial task    â”‚ 4-6   â”‚ 3-4   â”‚ Need all viewsâ”‚
â”‚ planning        â”‚       â”‚        â”‚               â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ Simple tool     â”‚ 1     â”‚ 0     â”‚ No debate     â”‚
â”‚ call (ls, cat)  â”‚       â”‚        â”‚ needed        â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ Ambiguous tool  â”‚ 2-3   â”‚ 2     â”‚ Need          â”‚
â”‚ result          â”‚       â”‚        â”‚ interpretationâ”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ Security findingâ”‚ 3-4   â”‚ 2-3   â”‚ Validate:     â”‚
â”‚ detected        â”‚       â”‚        â”‚ real or FP?   â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ Error/failure   â”‚ 2-3   â”‚ 2     â”‚ Diagnose +    â”‚
â”‚                 â”‚       â”‚        â”‚ alternate pathâ”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ Mid-plan step   â”‚ 1-2   â”‚ 0-1   â”‚ Plan already  â”‚
â”‚ (following plan)â”‚       â”‚        â”‚ exists        â”‚
â”œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¤
â”‚ Final synthesis/â”‚ 4-5   â”‚ 3     â”‚ Quality +     â”‚
â”‚ report writing  â”‚       â”‚        â”‚ completeness  â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### The Escalation/De-escalation Logic:

```text
After EACH multi-agent round, evaluate:

IF confidence > 0.9 AND consensus > 0.85:
   â†’ DE-ESCALATE: Drop to single agent for next call
   â†’ Store the plan, let executor run autonomously

IF confidence > 0.7 AND consensus > 0.7:
   â†’ MAINTAIN: Keep current agent count
   
IF confidence < 0.7 OR consensus < 0.6:
   â†’ ESCALATE: Add 1-2 more agents, add another round
   
IF error detected OR unexpected result:
   â†’ ESCALATE: Bring back full panel to reassess

Track "plan_stability" across calls:
   â†’ If plan hasn't changed in 3+ calls, drop to single agent
   â†’ If plan changes every call, escalate to full panel
```

---

## Putting It All Together â€” Full Agent-Zero Integration

```text
USER: "Test WebSocket security on target X"

CALL 1 â€” Planning (HIGH complexity)
â”œâ”€â”€ Router: 6 agents, 3 rounds
â”œâ”€â”€ War Room executes (~35 sec)
â”œâ”€â”€ Output: 8-step plan with priorities
â”œâ”€â”€ Confidence: 0.88
â””â”€â”€ Store plan in memory

CALL 2 â€” Research (LOW complexity)  
â”œâ”€â”€ Router: 1 agent
â”œâ”€â”€ Single call (~5 sec)
â”œâ”€â”€ Output: search_engine tool call
â””â”€â”€ Execute tool

CALL 3 â€” Research results (MEDIUM complexity)
â”œâ”€â”€ Router: 3 agents, 2 rounds  
â”œâ”€â”€ War Room executes (~20 sec)
â”œâ”€â”€ Output: "Research sufficient, proceed to endpoint discovery"
â”œâ”€â”€ Confidence: 0.82
â””â”€â”€ Following plan â†’ de-escalate

CALL 4 â€” Execute JS analysis (LOW â€” following plan)
â”œâ”€â”€ Router: 1 agent
â”œâ”€â”€ Output: code_execution_tool call
â””â”€â”€ Execute tool

CALL 5 â€” Analyze JS results (MEDIUM â€” interpreting findings)
â”œâ”€â”€ Router: 2 agents, 1 round
â”œâ”€â”€ Output: "Found 3 WebSocket endpoints, one suspicious"
â”œâ”€â”€ Confidence: 0.91 â†’ de-escalate further
â””â”€â”€ Continue plan

CALL 6 â€” Test suspicious endpoint (LOW â€” clear next step)
â”œâ”€â”€ Router: 1 agent
â”œâ”€â”€ Output: Burp MCP tool call with specific payload
â””â”€â”€ Execute tool

CALL 7 â€” Potential vulnerability found! (HIGH â€” escalate!)
â”œâ”€â”€ Router: 4 agents, 3 rounds (ESCALATED)
â”œâ”€â”€ War Room: Validate finding, assess severity, plan exploitation
â”œâ”€â”€ Output: "Confirmed CSWSH, CVSS 7.2, next: test auth bypass chain"
â””â”€â”€ Re-plan if needed

... and so on
```

---

## Key Design Principles Summary

| Principle | Implementation |
| --- | --- |
| **Never sequential** | All agents in each round fire via parallel async API calls |
| **Short + many rounds > long + few rounds** | Cap responses at 80-100 words, allow 3-5 rounds |
| **Agents reference each other** | Blackboard contains all prior outputs, prompts say "react to specific points" |
| **Smart about when to use it** | Complexity Router pre-classifies every call; most calls stay single-agent |
| **Escalate/de-escalate dynamically** | Confidence + consensus scores drive agent count up or down |
| **Time-boxed** | Hard cutoff: if rounds exceed 60 seconds, force synthesis immediately |
| **Staggered streaming** | Fast agents start round 2 before slow agents finish round 1 |
| **Plan persistence** | Once a plan is agreed upon, subsequent calls just execute it (single agent) until something unexpected happens |

This gives you the **group discussion feel** (agents reacting to each other, positions evolving, dissent being resolved) while staying within your **1-2 minute budget** and being **intelligent about when the full panel is actually needed**.
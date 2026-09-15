---
name: chat
description: Design consultation — explore approaches, trade-offs, and patterns before committing to an implementation. No code written. Outputs a structured comparison and a recommended next step.
argument-hint: <topic-or-problem>
---

# Design Consultation

Think through a design problem before picking an approach. No code is written. Output is a structured comparison of options and a concrete recommendation tied to the user's priorities.

## Arguments

- `$0` -- Topic or problem (e.g., "how to reap stuck reservations", "whether to add a PostgreSQL ledger for top-ups")

## Step 1: Clarify context

Before researching anything, ask:

1. **Priority axis**: speed to ship vs. long-term maintainability — where does this sit?
2. **Scale and lifespan**: expected load (generations per second, users), how long this code will be owned and evolved?
3. **Integration constraints**: what must not break? The given module is fixed; the PostgreSQL budget (accesses ≪ generations) is fixed; which other decisions in `README.md` are fixed?

Wait for answers. Do not proceed to Step 2 on assumption.

## Step 2: Research the codebase

Run `/research <topic>` to establish:
- What patterns already handle similar problems (CAS loops, Lua scripts, write-behind flusher, cold load)
- What the layer boundaries and DI approach imply about the decision
- What downstream code would be affected by each option

Read `README.md`: many alternatives were already weighed there, with reasons.

## Step 3: Survey established approaches

Draw from design literature based on what the problem class is:

- **Hexagonal / Clean Architecture**: dependency rule, port/adapter isolation — relevant for coupling and layering decisions
- **Optimistic vs pessimistic concurrency**: version CAS (what this project uses) vs locks — relevant for any new balance mutation
- **Write-behind vs write-through vs outbox/ledger**: durability window vs PostgreSQL load — relevant for anything that must eventually reach PostgreSQL
- **Idempotency keys**: cache the outcome vs retry — relevant for any new operation id
- **Simple direct code**: sometimes the boring answer is correct — relevant when the scope is small

For each candidate approach, evaluate:
- What it optimizes for
- What it costs (complexity, new abstractions, extra PostgreSQL statements, new Redis keys)
- Whether an existing file in the codebase already demonstrates this pattern

## Step 4: Present options

Output this structure — no freeform prose:

```markdown
# Design: {topic}

## Context
{User's stated priorities and constraints}

## Codebase baseline
{Relevant existing pattern + file path, or "no prior art"}

## Option A: {Name}
**Approach**: {1–2 sentences}
**Fits existing patterns**: yes | partial | no — {reason}
**Trade-offs**:
- Pro: {benefit}
- Con: {cost}

## Option B: {Name}
**Approach**: {1–2 sentences}
**Fits existing patterns**: yes | partial | no — {reason}
**Trade-offs**:
- Pro: {benefit}
- Con: {cost}

## Recommendation
**Option {X}** — {1–2 sentences tied directly to the user's stated priority axis}

## Next step
{One of: `/impl tuff <feature>`, `/impl mid <feature>`, `/plan <feature>`, or "discuss more before deciding"}
```

## What NOT to do

- Do not write code or implementation plans — this is exploration only
- Do not recommend an option without tying it to the user's priorities from Step 1
- Do not present more than 3 options — pick the realistic ones
- Do not skip Step 1 — recommendations without stated priorities are noise

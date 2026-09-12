---
name: research
description: Thoroughly investigate a part of the codebase before making changes. Use to understand existing patterns, trace data flows, and find reusable code.
argument-hint: <topic-or-area>
---

# Research the Codebase

Investigate a topic thoroughly. Read files, trace flows, find patterns. No code is written.

## Arguments

- `$0` -- Topic or area to research (e.g., "reservation flow", "how the flusher clears dirty users", "cold load")

## Investigation Checklist

### 1. Map the domain

Find all files related to the topic:

- Given module: `backend/domain/generation.py` — contracts, policy, access errors, fake provider (read-only)
- Entities: `find backend/domain/entities -name "*.py"` — read relevant ones
- Repos: `find backend/domain/repos -name "*.py"` — read Protocol interfaces
- Ports + value types: `find backend/app/shared/ports -name "*.py"`
- Services: `find backend/app/billing -name "*.py"` — read use cases
- Redis adapter + Lua: `backend/infra/database/redis/adapters/balance_store.py`, `backend/infra/database/redis/scripts.py`
- Composition root: `backend/entry/ioc.py`, `backend/entry/flusher.py`, `backend/main/cli.py`
- Decisions: `docs/DECISIONS.md`, plan: `docs/PLAN.md`

### 2. Trace a full operation flow

Pick a representative operation and trace it end to end:

1. **Caller** — test helper or process entry, which container scope it opens
2. **Service** — `execute_generation` / `apply_top_up` / `flush_once`: loop structure, retries, error mapping
3. **Loader** — cold load, lock, PostgreSQL read, seed
4. **Store** — which Lua script, KEYS/ARGV, outcome tags, dirty marking
5. **Repository** — which statement, version guard
6. **Entity / Redis keys** — what state is written where

### 3. Identify reusable patterns

- Existing value types (`BalanceSnapshot`, `GenerationRecord`, outcome classes)
- Existing services that already handle related logic (`BalanceLoader` for cold load)
- Existing Lua conventions and scripts that cover the needed atomic step
- Existing helpers in `tests/integration/billing/helpers.py`

### 4. Note gaps

- Missing store/repo methods that will be needed
- Missing value types or config fields
- Missing DI wiring
- Missing tests for the invariant

## Output Format

```markdown
# Research: {topic}

## Existing Components
- {component}: {file path} — {what it does}

## Data Flow
{caller} -> {service} -> {loader?} -> {store / Lua script} -> {redis keys}
                                   -> {repo} -> {postgres table}

## Patterns to Reuse
- {pattern}: {file path} — {why it's relevant}

## Gaps
- {what's missing}: {where it should go}

## Recommendations
- {suggestion for implementation approach}
```

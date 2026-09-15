---
name: plan
description: Produce a detailed implementation plan for a feature. MUST be used before implementing any non-trivial change. Outputs a structured plan — does NOT write code.
argument-hint: <feature-description>
---

# Plan a Feature

Produce a complete, reviewable implementation plan. No code is written.

## Arguments

- `$0` -- Feature description (e.g., "add a PostgreSQL audit ledger written by the flusher")

## Step 1: Understand — ask questions FIRST

Before doing ANY research, identify what you don't know. Ask the user about:

- **Business rules**: what are the exact requirements? what happens on edge cases (redelivery, provider failure, cold cache)?
- **Scope**: which services change? which process runs it (test caller, flusher)?
- **Data model**: what fields? Redis keys or PostgreSQL columns? version/idempotency semantics?
- **Side effects**: does it mutate balances? must it be atomic with an existing Lua script?
- **Error cases**: what should happen on invalid input? contention? missing balance?
- **Naming**: what should entities/ports/services be called?

Ask at least 3 clarifying questions. Wait for answers before proceeding.

## Step 2: Research existing code

After questions are answered, explore the codebase:

```
What entities exist?
```
!`find backend/domain/entities -name "*.py" -not -name "__init__.py" -not -path "*base*" -not -path "*__pycache__*" 2>/dev/null | sort`

```
What services exist?
```
!`find backend/app/billing -name "*.py" -not -name "__init__.py" -not -path "*__pycache__*" 2>/dev/null | sort`

```
What ports exist?
```
!`find backend/app/shared/ports -name "*.py" -not -name "__init__.py" -not -path "*__pycache__*" 2>/dev/null | sort`

```
What Lua scripts exist?
```
!`grep -n "^[A-Z_]* = " backend/infra/database/redis/scripts.py 2>/dev/null`

Read the relevant existing files and `README.md` to understand current patterns and what can be reused.

## Step 3: Produce the plan

Output a structured plan in this exact format:

```markdown
# Plan: {Feature Name}

## Summary
{1-2 sentences describing the feature}

## Questions Resolved
- Q: {question} -> A: {answer from user}
- ...

## Components (in implementation order)

### 1. Entity: {EntityName} (if needed)
- File: backend/domain/entities/{name}.py
- Columns:
  - {field}: Mapped[{type}] ({constraints, server_default})
- Migration: hand-written revision "{description}"

### 2. Repository: {Entity}Repo (if needed)
- Protocol: backend/domain/repos/{name}.py
- Impl: backend/infra/database/psql/repos/{name}.py
- Methods:
  - {method}({params}) -> Option[{Entity}] | None
- Gateway property name: {name}

### 3. Port: {PortName} (if needed)
- Protocol: backend/app/shared/ports/billing/{name}.py
- Methods and value types
- Adapter: backend/infra/database/redis/adapters/{name}.py (Impl class name)
- Lua scripts: KEYS/ARGV layout, return tags

### 4. Service: {Name}Service / method on an existing service
- File: backend/app/billing/{name}.py
- Signature: {method}({params}) -> {return_type}
- Dependencies: store, loader, db, config, ...
- Algorithm:
  1. {step}
  2. {step}
- Error cases:
  - {condition} -> {ErrorType}(message="{message}")

### 5. Config (if needed)
- New BillingConfig fields with defaults; .env.example entries

### 6. DI Wiring
- File: backend/entry/ioc.py — new providers or bindings

### 7. Tests
- tests/integration/billing/test_{topic}.py: {test names and the invariant each asserts}
- tests/unit/billing/... for pure arithmetic (if any)

## Files Created/Modified (checklist)
- [ ] ... (CREATE / MODIFY)

## Verification
- [ ] `just check` passes
- [ ] `just test` / `just test-unit` pass
- [ ] `backend/domain/generation.py` unchanged
- [ ] PostgreSQL statement budget still holds ({expected counts})
```

## Step 4: Wait for approval

Present the plan and explicitly ask: "Does this plan look correct? Any changes before I implement?"

Do NOT proceed to implementation until the user approves.

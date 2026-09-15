---
name: update-knowledge
description: Review and update .claude/ configuration to reflect the current state of the project. Use after adding new services, ports, patterns, conventions, or when the user corrects an approach.
argument-hint: [topic]
---

# Update Project Knowledge

Review the `.claude/` configuration against the actual codebase and propose updates.

## Arguments

- `$0` (optional) -- Specific topic to update (e.g., "new ledger port", "flusher batching convention"). If omitted, do a full review.

## Step 1: Discover what exists now

Current entities:
!`find backend/domain/entities -name "*.py" -not -name "__init__.py" -not -path "*base*" -not -path "*__pycache__*" 2>/dev/null | sort`

Current services:
!`find backend/app/billing -name "*.py" -not -name "__init__.py" -not -path "*__pycache__*" 2>/dev/null | sort`

Current ports:
!`find backend/app/shared/ports -name "*.py" -not -name "__init__.py" -not -path "*__pycache__*" 2>/dev/null | sort`

Current adapters:
!`find backend/infra/database -name "*.py" -path "*adapters*" -not -name "__init__.py" -not -path "*__pycache__*" 2>/dev/null | sort`

Current Lua scripts:
!`grep -n "^[A-Z_]* = " backend/infra/database/redis/scripts.py 2>/dev/null`

Current tests:
!`find tests -name "test_*.py" -not -path "*__pycache__*" 2>/dev/null | sort`

Recent git changes:
!`git log --oneline -15 2>/dev/null`

## Step 2: Compare against documented knowledge

Read each of these and check if they're still accurate:

1. `.claude/CLAUDE.md` — does the architecture tree match reality? Any new patterns?
2. `.claude/rules/*.md` — do code examples still match current code? Any new conventions?
3. `.claude/skills/*/SKILL.md` — do the `!find` commands reflect current structure? Any new skill needed?
4. `.claude/agents/*.md` — do review checklists cover all current patterns?
5. `README.md` — are new decisions or deviations recorded?

## Step 3: Identify gaps

For each gap found, categorize it:

- **Stale**: documented but no longer true (renamed, removed, changed)
- **Missing**: exists in code but not documented
- **New pattern**: emerged from recent work, should be captured

## Step 4: Propose changes

For each update, present:

```markdown
### Update: {file path}

**Why**: {what changed and why the docs need updating}

**Current** (line N):
> {current text}

**Proposed**:
> {new text}
```

## Step 5: Apply approved changes

After the user approves, update the files. For each change:
1. Read the target file
2. Make the specific edit
3. Verify the file is consistent after the edit

## When to use this skill

- After adding a new service, port, adapter or Lua script
- After the user corrects your approach on something non-obvious
- After changing a concurrency or storage pattern (record in `README.md` too)
- After changing an established pattern
- Periodically (every few sessions) as a hygiene check

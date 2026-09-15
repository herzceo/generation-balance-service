---
name: help
description: Show an overview of this project — what it is, available commands, and how to start.
---

# Help

Output the following text verbatim:

---

## Generations and balances service

Test assignment (`TASK.md`): paid generations against a fake provider, balances in Redis with
write-behind to PostgreSQL. Stack: Python 3.12+ · SQLAlchemy 2.0 async · Dishka DI · msgspec · PostgreSQL · Redis (Lua) · Alembic · pytest + testcontainers.

---

### Getting started

1. `uv sync` and copy `.env.example` to `.env`
2. `just start` (PostgreSQL + Redis in Docker), `just migrate`
3. `just test` runs the integration suite; `just flusher` runs the write-behind process
4. Read `README.md` before changing any balance logic

---

### Commands

**Implementation**
- `/impl [small|mid|tuff] <change>` — implement anything; Claude auto-detects the right pipeline (small = in-place edit, mid = chain of /add-* skills, tuff = full planning cycle with review); pass a tier to override
- `/chat <problem>` — design consultation before committing to an approach; Claude researches patterns and presents options with trade-offs; no code written

**Planning**
- `/plan <feature>` — produce a detailed implementation plan; used inside the tuff pipeline
- `/research <topic>` — investigate the codebase before making changes

**Building blocks** (used inside `/impl mid`, or directly)
- `/add-entity <name> <fields>` — domain entity + hand-written migration
- `/add-repository <entity>` — protocol + impl + gateway wire
- `/add-port <category> <name>` — protocol + adapter + DI wire
- `/add-migration <description>` — Alembic migration + drift test
- `/add-service <domain> <name>` — application service
- `/add-test <domain> <scenario>` — integration or unit test

**Testing**
- `/run-tests <domain | path | category>` — scoped test run, faster than `just test`
- `/debug-failing-test <test-path>` — diagnose a failing test with project-specific recipes

**Knowledge**
- `/update-knowledge [topic]` — update `.claude/` docs to reflect current codebase state
- `/explain <question>` — quick answer from `.claude/` docs; no codebase scan

---

### Shell commands

```
just check          ruff format + ruff check + mypy + layer guard
just test           integration tests (requires Docker)
just test-unit      unit tests (no Docker)
just test-all       both
just flusher        run the write-behind flusher process
just migrate        apply pending migrations
just migration "x"  autogenerate a new migration (needs a reachable PostgreSQL)
just start          start Docker Compose stack (PostgreSQL + Redis)
just stop           stop Docker Compose stack
```

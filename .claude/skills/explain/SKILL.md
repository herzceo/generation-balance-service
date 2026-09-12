---
name: explain
description: Answer a quick question about the template using only .claude/ docs — no codebase scan
argument-hint: <question>
disable-model-invocation: true
---

# Explain a Project Concept

Answer a quick question about why the project is structured the way it is. Answers come only from the `.claude/` docs — no codebase scan, no file reading beyond the docs. Fast and low-effort by design.

**This skill is for simple conceptual questions.** If you need to trace actual code, understand a specific flow in detail, or investigate something that may have changed since the docs were last updated, use `/research <topic>` instead.

## Arguments

- `$ARGUMENTS` — the question (e.g. "why is there a domain and app layer", "what is a port", "how does DI work")

## Step 1 — Guard: require a question

If `$ARGUMENTS` is empty, print:

```
Usage: /explain <question>

Examples:
  /explain why there is a domain and app layer
  /explain what is a port and why not just use a class
  /explain how does dependency injection work
  /explain why balances live in Redis and not PostgreSQL

For deep code tracing, use /research <topic> instead.
```

Then stop.

## Step 2 — Identify relevant rule files

Check the question for keywords and select the 1–3 most relevant rule files to read. Do not read all rules — pick only what matches.

| Keywords in question | Rule file |
|----------------------|-----------|
| layer, import direction, domain, app, entry, infra, internal, why separate | `architecture.md` |
| entity, table, ORM, column, mapped, Numeric, natural key | `entities.md` |
| repository, repo, gateway, upsert, ImplRepoGateway | `repositories.md` |
| port, adapter, protocol, abstraction, dependency inversion, Redis, Lua, CAS, version, key layout | `ports-adapters.md` |
| DI, dependency injection, provider, Dishka, scope, wire, container | `dependency-injection.md` |
| error, exception, Option, DetailedError, some, access error, refund | `error-handling.md` |
| service, loader, flusher, reservation, top-up, write-behind, cold load | `CLAUDE.md` Key Patterns + `docs/DECISIONS.md` |
| test, integration test, unit test, fixture, multiprocess, statement count | `testing.md` |
| type, mypy, generic, typing, annotation, given module | `typing.md` |
| migration, Alembic, schema, database, transaction, session | `database.md` |
| style, ruff, format, lint, comment, naming | `code-style.md` |
| git, commit, branch, PR, workflow | `git.md` |

If no keyword clearly matches, read only `CLAUDE.md` (the Key Patterns and Architecture sections).

Rule files live at: `.claude/rules/<file>`

List what's available first if uncertain:
`! ls .claude/rules/`

## Step 3 — Read and answer

Read the selected files. Also read the **Architecture** and **Key Patterns** sections of `.claude/CLAUDE.md` if the question is architectural.

Answer the question:
- 2–5 sentences
- Direct — state the reason, not just what the thing is
- No bullet lists unless the answer is inherently enumerable
- No preamble ("Great question!", "Based on the docs...")

## Step 4 — Disclaimer

End every answer with this line, verbatim:

> *This answer is drawn from `.claude/` docs only. For questions that require tracing actual code, run `/research <topic>`.*

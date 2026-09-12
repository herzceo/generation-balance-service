---
name: run-tests
description: Run integration or unit tests scoped to a domain, file, or category. Faster feedback than `just test`. Reports failures and points to /debug-failing-test.
argument-hint: <domain | path | category> [test-name-pattern]
---

# Run Tests Scoped to a Domain

Run a focused subset of the test suite. Use after editing services, the adapter or the flusher to
get feedback in seconds instead of running the full suite.

## Arguments

- `$0` — domain name, relative path, or category. See resolution table below.
- `$1` (optional) — test-name pattern passed to pytest `-k`. Example: `redelivery_in_flight`.

## Path resolution

| Argument | Resolves to |
|----------|-------------|
| `billing` | `tests/integration/billing/` |
| `billing/single` | `tests/integration/billing/test_single.py` |
| `billing/parallel` | `tests/integration/billing/test_parallel.py` |
| `billing/redelivery` | `tests/integration/billing/test_redelivery.py` |
| `billing/provider_errors` | `tests/integration/billing/test_provider_errors.py` |
| `billing/top_up` | `tests/integration/billing/test_top_up.py` |
| `billing/cold_cache` | `tests/integration/billing/test_cold_cache.py` |
| `billing/pg_budget` | `tests/integration/billing/test_pg_budget.py` |
| `billing/convergence` | `tests/integration/billing/test_convergence.py` |
| `billing/multiprocess` | `tests/integration/billing/test_multiprocess.py` |
| `migrations` | `tests/integration/test_migrations.py` |
| `unit` | `tests/unit/` |
| `all` | `tests/integration/` |

If the argument starts with `tests/`, treat it as an explicit path and pass through unchanged.

If the resolved path doesn't exist, list available files and stop:

!`ls tests/integration/billing/ 2>/dev/null`

## Pre-flight

Integration tests need Docker (testcontainers spawn postgres + redis):

!`docker info >/dev/null 2>&1 && echo "docker: ok" || echo "docker: NOT RUNNING — start Docker Desktop before integration tests"`

If Docker is down and the target is integration, stop and tell the user. Unit tests never need Docker.

## Execution

```bash
uv run pytest <resolved-path> -x --tb=short [-k <pattern>]
```

Flags:
- `-x` stop on first failure (fail fast for tighter feedback)
- `--tb=short` compact tracebacks
- `-v -s` are already in `addopts`; the PG statement counts printed by `test_pg_budget.py` and
  `test_multiprocess.py` show up in the output

Why bypass nox: nox re-runs `uv sync --group=dev --frozen` every invocation. For iterative debugging that's slow. The dev group is normally already in sync — if pytest is missing, suggest `uv sync --group=dev`.

## On success

Report a one-liner:

```
✓ N tests passed in {scope} ({duration})
```

## On failure

1. Show the failing test name and a 3-5 line error excerpt.
2. Suggest: `run /debug-failing-test {failing-test-path}` for guided diagnosis.
3. Do NOT auto-fix — diagnosis and remediation belong to `/debug-failing-test`.

## When NOT to use

- For full pre-merge confidence — use `just test` (whole integration suite via nox).
- For migrations testing — the session fixture applies them; `migrations` runs the drift check only.

## Common scopes

```
/run-tests billing                       # every billing scenario
/run-tests billing/parallel              # one file
/run-tests billing redelivery_in_flight  # one test pattern in billing/
/run-tests unit                          # pure logic tests, no Docker
```

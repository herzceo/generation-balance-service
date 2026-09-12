---
name: debug-failing-test
description: Diagnose a failing integration or unit test using project-specific recipes. Walks through known failure categories from rules/testing.md before suggesting a fix.
argument-hint: <test-path-or-pattern>
---

# Debug a Failing Test

Walk a failing test through known failure categories for this project's testcontainers-based suite. Pattern-match symptoms to recipes, apply the smallest viable fix, re-run.

## Arguments

- `$0` — pytest path or `-k` pattern. Examples:
  - `tests/integration/billing/test_parallel.py::test_parallel_premium_exactly_k_succeed`
  - `test_parallel_premium_exactly_k_succeed` (will be passed as `-k`)
  - `billing/parallel` (resolved via `/run-tests` path rules)

## Step 1: Reproduce with full context

```bash
uv run pytest <arg> -x --tb=long --showlocals
```

`--showlocals` exposes fixture state at the failure point — invaluable for diagnosing balance
snapshots and provider stats.

Capture the FULL traceback. The exception type plus the first frame in user code drive the next step.

## Step 2: Classify the failure

Match the symptom to one category below. Apply only the matching recipe — don't shotgun fixes.

### A. Container / Docker / infra failures

**Symptoms**:
- `docker.errors.DockerException`
- `Could not connect to PostgreSQL` / `redis.exceptions.ConnectionError` during fixture setup
- Tests stuck on collection
- `error in fixture postgres_url` / `redis_url`
- Ryuk: `Port mapping for container ... and port 8080 is not available`

**Recipe**:
1. `docker info >/dev/null 2>&1 && echo ok` — verify daemon.
2. If a prior testcontainer is wedged: `docker ps` and `docker rm -f <id>`.
3. Ryuk port clash right after Docker started: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest ...`.
4. Re-run.

### B. Schema / migration mismatch

**Symptoms**:
- `UndefinedTable: relation "..." does not exist`
- `column "..." does not exist`
- `test_schema_matches_entities` reports a non-empty diff

**Recipe**:
1. Verify the entity exists in `backend/domain/entities/` and is exported from `__init__.py`.
2. Check `backend/infra/database/psql/alembic/migrations/versions/` for the revision.
3. Fix the migration (types, scale, server defaults, constraint names via `op.f`) to match the entity.
4. The session-scoped `postgres_url` fixture runs `alembic upgrade head` ONCE per pytest session; re-run from a clean shell after changing a migration.

### C. Redis state leak / cross-test contamination

**Symptoms**:
- Balance already has a value before the test seeded anything
- `bal:dirty` not empty at the start / after a flush with "extra" users
- A `gen:{id}` record exists before the first call (`Duplicate` where `Reserved` was expected)
- Test passes alone but fails in the full suite

**Recipe**:
- The `redis` fixture must `flushdb()` before yielding and stay function-scoped.
- Use a fresh `uuid4()` user and fresh `client_request_id`s per test; never module-level constants.
- The `container`/`engine`/`redis` fixtures must never be promoted to session scope.

### D. Multi-process (spawn) failures

**Symptoms**:
- `AttributeError: Can't get attribute '_run_worker' on <module ...>` / `PicklingError`
- `multiprocessing.managers.RemoteError` / `AuthenticationError` in a child
- `EOFError` / child hangs; `RuntimeError: An attempt has been made to start a new process before the current process has finished its bootstrapping phase`

**Recipe**:
- The worker function must be **module-level** in the test module (spawn imports it by name); no lambdas or closures.
- Arguments must be picklable: URLs and ids as `str`, requests as tuples, plus the `SharedProviderState` proxies. Return builtins only.
- The `manager` fixture must be the session-scoped `multiprocessing.Manager()` started in the parent; proxies inherit its authkey through spawn. Never create a second Manager in the child.
- Always `multiprocessing.get_context("spawn")`; call `pool.close(); pool.join()` (or use the context manager).
- The child builds its own engine/redis/container and runs `asyncio.run(...)`; never pass the parent's engine or container.

### E. Fixture scope / isolation issues

**Symptoms**:
- `provider.get_stats().calls` contains ids from other tests
- `pg_counter.count` higher than expected from the first statement

**Recipe**:
- `shared_state`/`provider` are function-scoped so provider counters reset per test.
- Call `pg_counter.reset()` after `seed_pg_balance(...)`; seeding is a PG write and must not count.
- Warm vs cold: if the test expects `count == 1` for the cold SELECT, make sure nothing loaded the balance into Redis earlier in the test.

### F. Assertion mismatches (wrong amount / wrong exception)

**Symptoms**:
- Expected `InsufficientBalanceError`, got `FreeRequestsExhaustedError` (or vice versa)
- Balance off by one debit
- `Decimal("0.05") != Decimal("0.050000")` style surprises (they are equal in Python — check the other operand)

**Recipe**:
- Re-derive the expected outcome from `DebitPolicyService.authorize` in `backend/domain/generation.py`: once `paid_usd` reaches 0, `has_paid_balance` flips and a `basic`/`conditional` request consumes a free request, so with `free_requests == 0` the error is `FreeRequestsExhaustedError`, not `InsufficientBalanceError`.
- Sum `billed_cost_usd` over successful results and compare with `initial − final` per bucket.
- Check exception classes, never message text.

### G. Async / fixture wiring issues

**Symptoms**:
- `RuntimeError: Event loop is closed`
- `RuntimeWarning: coroutine '...' was never awaited`
- Fixture function returns `<coroutine object>` instead of value
- Background flusher task never cancelled → test hangs at teardown

**Recipe**:
- Test functions touching async code must be `async def`.
- Async fixtures yielding resources: `async def fixture():` + `yield value` in `async with` / try-finally.
- Cancel every `asyncio.create_task(flusher.run())` and await it under `contextlib.suppress(asyncio.CancelledError)` before the test returns.
- Inspect `pyproject.toml` `[tool.pytest.ini_options]` — `asyncio_mode = "auto"`.

### H. Layer guard failures (during `just check`, not test runtime)

**Symptoms**:
- `[guard_layers] layer violation in ...`

**Recipe**:
- This is not a test failure — it's the static guard. The hook caught an import like `from backend.infra.X import Y` inside `app/`, or `backend.app.billing.*` inside `infra/`.
- Move the implementation to satisfy the layer. Reference: `rules/architecture.md`.

### I. Timing flakiness

**Symptoms**:
- `max_active_calls` assertion fails intermittently
- In-flight redelivery sometimes executes the provider twice
- Background flusher test times out on slow Docker

**Recipe**:
- Replace any bare `asyncio.sleep` with `wait_until(predicate, timeout, interval)`.
- Before sending an in-flight duplicate, wait for `provider.get_stats().active_calls >= 1`.
- Assert `max_active_calls >= 2`, never an exact number.
- Raise `wait_until` timeouts (5 s) rather than shrinking scenario delays below 0.1 s.

## Step 3: Apply the smallest fix and re-run JUST the failing test

```bash
uv run pytest <arg> -x
```

If it passes, run the whole file to confirm no regression:

```
/run-tests billing/<file>
```

## Step 4: When the recipe doesn't match

If none of A-I matches:

1. Read the failing test end-to-end.
2. Read a passing test in the same file. Diff fixture lists, helper calls, assertions. The difference is usually the fix.
3. If the test asserts on Redis state, inspect it directly through the `store` fixture (`store.load`, `store.get_generation`).
4. If still stuck after 10 minutes, surface the failure to the user with: traceback excerpt, what you tried, what you suspect.

## What you must NOT do

- Don't disable a failing test (`pytest.skip`, `xfail`) without user approval — the test exists for a reason.
- Don't relax the assertion to make it pass. If the system behaves differently than the test expects, the *system* is the bug or the *test* needs an explicit fix with rationale.
- Don't change `rules/testing.md` or the fixtures unless the user explicitly says so. Test infrastructure is shared — break it and every test breaks.

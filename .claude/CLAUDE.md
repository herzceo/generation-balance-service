# Generations and balances service

Test assignment (`TASK.md`): a bot runs paid generations against a fake provider; user balances
live in Redis while the system runs and are written behind to PostgreSQL in batches. No HTTP API.
The application services are called directly from tests and from the `backend flusher` process.

## Workflow — assess effort first, then act

For any implementation request — whether a plain prompt or an explicit `/impl` call — classify the effort tier inline before acting. Do not invoke `/impl` as a skill; apply the pipeline directly. The user can type `/impl [small|mid|tuff]` to override the detected tier.

Classify the effort tier:

**small** — all of these are true:
- Scoped to 1–2 existing files
- No new files, no new abstractions
- Modifying a value, condition, field, or small logic block

**mid** — any of these is true (and not tuff):
- Existing entity or service extended with new behavior (method, repo query, Lua branch)
- New well described entity + repo, following established patterns
- One or more `/add-*` skills directly match the task
- 3–8 files, all following established patterns

**tuff** — any of these is true:
- New port, adapter, repo, or background process as a new abstraction
- No existing `/add-*` skill covers the task
- Request uses "new domain", "from scratch", "design", or "architect"
- Implementation is multi-layered and requires architectural decisions

When ambiguous between mid and tuff → mid. Between small and mid → mid.

### small pipeline
1. Read the affected file(s)
2. Ask at most one clarifying question if genuinely ambiguous — otherwise proceed immediately
3. Make the change
4. Run `just check` — fix all issues before reporting done

### mid pipeline
1. Identify which `/add-*` skills apply and their execution order
2. Run `/research <domain>` — confirm existing patterns, never invent when an example exists
3. Execute the `/add-*` skills in dependency order (entity → repo → port → service → test)
4. Run `just check` — fix all issues
5. Delegate to **implementation-verifier** agent — fix any drift before reporting done

No plan file. No approval step.

### tuff pipeline
1. **Understand**: ask clarifying questions — business rules, scope, data model, side effects, error cases, naming (at least 2–3 questions)
2. **Research**: use `/research <topic>` — read all existing code that touches the domain
3. **Plan**: use `/plan <feature>` — every file, field, method signature, error case; no hand-waving
4. **Confirm (plan mode only)**: if the session is running in plan mode, call `ExitPlanMode` to present the plan and wait for approval. Outside plan mode, skip — proceed directly to implementation.
5. **Implement**: follow the plan exactly, in dependency order — run `just check` after each major step
6. **Verify**: use the implementation-verifier agent — every planned file must exist, `just check` must pass
7. **Audit**: use the architecture-reviewer agent — fix any layer violations or pattern drift before reporting done

Run the **plan-reviewer** agent only when the user explicitly asks for a plan review (e.g. "review the plan").

The user can type `/impl [small|mid|tuff] <description>` to explicitly force a tier.

## Knowledge Maintenance — keep `.claude/` in sync with the project

The `.claude/` configuration is a living document. Knowledge updates can happen at any point in any pipeline tier — during research, mid-implementation, or after verification. Apply these updates directly — no approval step, self-accept them:

- **New pattern discovered** during implementation? Add it to the relevant rule in `rules/` or to CLAUDE.md Key Patterns.
- **New convention agreed** with the user (naming, structure, approach)? Update the relevant rule.
- **New domain added** (entity, service, port)? Update skill templates if the new domain introduced a variation.
- **User corrects your approach**? Capture the correction as a rule so it's not repeated.
- **New storage or concurrency pattern**? Update `rules/ports-adapters.md` or `rules/database.md`.
- **New error type or handling pattern**? Update `rules/error-handling.md`.

Self-accept `.claude/` updates without asking — but always report what you changed: name the file, the section, and the change. Be specific. Don't silently absorb lessons — surface them so future sessions benefit too.

## Stack
Python 3.12-3.13 | SQLAlchemy 2.0 (async, psycopg) | Dishka DI | msgspec DTOs | PostgreSQL | Redis (Lua scripts) | Alembic | pytest + testcontainers

## Architecture

```
backend/
├── domain/
│   ├── generation.py                 given module from TASK.md, byte-identical, never edit
│   ├── entities/{__init__.py, balance.py, base/{__init__.py, base.py}}
│   └── repos/{__init__.py, balance.py, gateway.py}
├── app/
│   ├── errors.py                     DetailedError hierarchy + billing errors
│   ├── billing/{__init__.py, config.py, balance_loader.py, generation.py, balance.py, refund.py, reaper.py, flusher.py}
│   └── shared/
│       ├── db/{__init__.py, database.py}
│       └── ports/{__init__.py, billing/{__init__.py, balance_store.py, generation_provider.py}}
├── infra/
│   └── database/
│       ├── config.py
│       ├── psql/{__init__.py, database.py, engine.py,
│       │        repos/{__init__.py, balance.py, gateway.py},
│       │        alembic/{__init__.py, alembic.ini, migrations/{__init__.py, env.py, README, script.py.mako,
│       │                 versions/{__init__.py, <date>_<rev>_init_balance.py}}}}
│       └── redis/{__init__.py, config.py, client.py, scripts.py, adapters/{__init__.py, balance_store.py}}
├── entry/{__init__.py, ioc.py, flusher.py}
├── internal/{__init__.py, case.py, option.py, dto/{__init__.py, struct.py, types.py}}
└── main/{__init__.py, cli.py, utils/{__init__.py, load_from_env.py}}
tests/
├── unit/{internal/test_option.py, billing/{test_balance_snapshot.py, test_surplus_refund.py},
│         entry/test_ioc.py, main/test_load_from_env.py}
└── integration/{conftest.py, test_migrations.py,
                 billing/{conftest.py, ioc.py, helpers.py, test_single.py, test_parallel.py, test_redelivery.py,
                          test_provider_errors.py, test_top_up.py, test_cold_cache.py, test_pg_budget.py,
                          test_convergence.py, test_multiprocess.py, test_settle_reconciliation.py, test_reaper.py,
                          test_transport_retries.py, test_flusher_conflicts.py}}
```

## Import Direction (strictly enforced by hooks)
- `domain/` imports only from `domain/`, `internal/`
- `app/` imports only from `app/`, `domain/`, `internal/` -- NEVER from `infra/` or `entry/`
- `entry/` imports from anything in `backend.*`
- `infra/` imports from `infra/`, `domain/`, `backend.app.shared.*` (ports, db), `internal/`
- `internal/` imports only from `internal/`
- Dependency inversion: `app/` defines Protocols in `app/shared/ports/`, `infra/` implements them

## Key Patterns

### Given module (domain/generation.py)
`backend/domain/generation.py` is `TASK.md` lines 16–329 committed byte-identical. It holds the policy (`DebitPolicyService`), the contracts (`GenerationRequest`, `BalanceTopUp`, `DebitPlan`, `Authorization`, `GenerationResult`, `BalanceView`), the access errors and `FakeGenerationProvider`. It is excluded from ruff (`[tool.ruff] exclude`) and has a mypy `ignore_errors` override (strict mypy rejects `order = ("paid",)` being reassigned a 3-tuple). Never edit it; `diff <(sed -n '16,329p' TASK.md) backend/domain/generation.py` must stay empty. Import its types from everywhere; never copy its logic.

### Entity (domain/entities/)
`Balance(Base)` with a natural primary key `user_id`, money columns `Numeric(18, 6)`, `free_requests`, `version`, `updated_at`, all with `server_default`. No mixins: the table is written only through a version-guarded upsert. `Base` derives the table name from the class name and carries the naming convention for indexes and constraints. Export every entity from `backend/domain/entities/__init__.py` so Alembic sees it.

### Repository (domain/repos/ + infra/database/psql/repos/)
Standalone Protocol per entity: `BalanceRepo` with `get_by_user_id(user_id) -> Option[Balance]` and `upsert_many(balances: list[Balance]) -> set[UUID]`. `ImplBalanceRepo` is `@final`, `__slots__ = ("_session",)`, takes an `AsyncSession`; `upsert_many` sorts rows by `user_id`, issues one `INSERT ... ON CONFLICT (user_id) DO UPDATE ... WHERE excluded.version > balance.version RETURNING user_id` and returns the accepted ids. Central access via `RepoGateway` Protocol + `ImplRepoGateway` with `@cached_property`. All lookups return `Option[T]`.

### Port / Adapter (app/shared/ports/billing/ + infra/database/redis/adapters/)
`BalanceStore` is the Redis port (load, seed_if_absent, load lock, advance_version, reserve, get_generation, settle, refund, top_up, stale_inflight, forget_inflight, dirty_users, clear_dirty) with value types `BalanceSnapshot` (non-frozen `StructDTO`, satisfies `BalanceView`; a read-only view, no arithmetic), `GenerationRecord`, and outcome dataclasses (`Reserved`, `Conflict`, `Missing`, `Duplicate`, `Applied`, `AlreadyApplied`, `Stale`). Only `reserve` is a CAS on `version` (`Conflict` exists only in `ReserveOutcome`); `settle`, `refund` and `top_up` are additive one-shot calls that take the amounts (`DebitPlan` / `BalanceTopUp`), never a snapshot. `settle` answers `AlreadyApplied` for a record that is already `done` and `Stale` only for one the reaper failed, so a retried settle whose first reply was lost is not reported as a failure. The cold-load lock is owner-checked: `try_acquire_load_lock` returns a token and `release_load_lock(user_id, token)` deletes only on a match. `GenerationProvider` is the provider port; `FakeGenerationProvider` satisfies it structurally, no wrapper. `ImplRedisBalanceStore` runs the Lua scripts from `infra/database/redis/scripts.py`. Balances live in Redis as integer micro-dollars (`money_to_micros` / `micros_to_money`, exact or `ValueError`), so Lua adds with `HINCRBY` and only `RESERVE` compares `version`; every mutation still bumps `version` so a concurrent `RESERVE` re-reads and the flusher sees it. Record amounts are micros too, except the raw `provider_billed_cost_usd` (decimal string, may exceed six decimals). Python keeps `Decimal`.

Redis keys: `bal:{user_id}` hash (`free_usd bonus_usd paid_usd` in integer micro-dollars, `free_requests version`, no TTL), `bal:dirty` set, `bal:load:{user_id}` cold-load lock (`SET NX PX`, value = owner token), `gen:{client_request_id}` record hash (status running/done/failed, plan, result, `reservation_token`; one TTL for every status), `gen:inflight` ZSET of running reservations scored by `started_at` (the reaper's index), `topup:{operation_id}` idempotency marker.

Every money command (`reserve`, `settle`, `refund`, `top_up`, `advance_version`, `forget_inflight`) is wrapped in `_retrying` (3 attempts on `ConnectionError`/`TimeoutError`). Each script recognises its own landed-but-unacknowledged call instead of applying it twice: `RESERVE` by the per-call `reservation_token` on the record, `TOP_UP` by the `topup:` marker, `SETTLE` by the `done` status, `REFUND` by the `status == running` guard.

### Services (app/billing/)
`@dataclass`, REQUEST-scoped, dependencies typed to Protocols. `BalanceLoader.ensure_loaded` (cold load behind the Redis lock, exactly one PG SELECT per cache miss), `GenerationService.execute_generation` (validate model → snapshot → `DebitPolicyService.authorize` → CAS `reserve` loop → provider call outside any lock under `asyncio.timeout(PROVIDER_TIMEOUT_SECONDS)`, which must stay below `REAP_AFTER_SECONDS` → one-shot `settle` (surplus below the plan refunded additively, LIFO along the charge order, authorization is a ceiling; `Stale` = reaped → `GenerationFailedError`) or `refund` via `ReservationRefunder`; duplicates resolved from the record, running ones awaited by polling; a denied redelivery still returns its cached outcome), `BalanceService.apply_top_up` / `get_balance` (validated, one additive idempotent call; the loop only handles `Missing` via a cold load), `ReservationReaper.reap_once` (refunds `running` records older than `REAP_AFTER_SECONDS` from `gen:inflight`), `BalanceFlusher.flush_once` / `run` (dirty users → one multi-row upsert returning the accepted ids → for each rejected row compare with the PG row and, when the state differs, `advance_version` past PG's (CAS on the read version, fails harmlessly in a two-flusher race) with a warning → version-conditional clear; `run` also calls `reap_once` every tick). Service knobs live in `BillingConfig` (`app/billing/config.py`); the adapter's TTLs (`GENERATION_RECORD_TTL_SECONDS`, `TOP_UP_RECORD_TTL_SECONDS`, `LOAD_LOCK_TTL_MS`) live in `RedisBalanceStoreConfig` next to `ImplRedisBalanceStore`. Both are APP singletons.

### Errors (app/errors.py)
`DetailedError(message, code, details)` hierarchy: `InvalidInputError`, `ConflictError`, plus `GenerationFailedError` (provider raised; cached per `client_request_id`), `GenerationInProgressError` (waiter deadline), `BalanceContentionError` (CAS/load retries exhausted). Access errors from the given module (`InsufficientBalanceError`, `FreeRequestsExhaustedError`, `ConditionalFreeAccessDeniedError`) propagate untouched. `Option[T].some(exc)` for repo lookups; `.value` where absence is a normal branch.

### Database (app/shared/db/)
`Database` Protocol = unit of work (`async with self.db:` opens a transaction, `commit()` explicit, no nesting) + `gateway` to repositories. `ImplDatabase` in `infra/database/psql/database.py`. Sessionmaker uses `expire_on_commit=False, autoflush=False`.

### Dependency Injection (entry/ioc.py)
`create_container(*, engine, redis, billing_config, store_config=None, provider=None)` composes Dishka providers: APP — engine, sessionmaker, `Redis`, `BillingConfig`, `RedisBalanceStoreConfig`, `DebitPolicyService`, `ImplRedisBalanceStore → BalanceStore`, `provider → GenerationProvider`; REQUEST — `ImplDatabase → Database`, `BalanceLoader`, `ReservationRefunder`, `ReservationReaper`, `BalanceService`, `BalanceFlusher`, plus `GenerationService` only when a `provider` was passed. Dishka validates the whole graph when the container is built, so registering `GenerationService` without a `GenerationProvider` breaks every container -- including the flusher process, which runs no generations. Engine and Redis lifetimes belong to the caller (`entry/flusher.py` or the test fixture). Bind impl to Protocol: `provider.provide(Impl, provides=Protocol)`. Config values travel as `StructDTO` config classes, never bare primitives.

### Migrations (infra/database/psql/alembic/)
One hand-written init migration creates `balance`. `tests/integration/test_migrations.py` asserts `compare_metadata(...) == []` against the migrated testcontainer database, so entity and migration cannot drift silently.

## Code Style
- ruff `select = ["ALL"]`, line-length 100. Strict mypy.
- No useless comments, no section dividers
- No function-level imports -- all top-level or `if TYPE_CHECKING:`
- `@final` on every concrete implementation class
- `ImplXxx` naming for implementations
- One primary class per file, explicit `__all__` exports in `__init__.py`
- Python 3.12+ generics: `class X[T]:` not `Generic[T]`
- All commands run through `uv run` -- never bare `python3` or `python`
- Money is `Decimal`, never `float`; a value arriving from outside (a provider bill) passes `quantize_money` (6 dp, `ROUND_DOWN`) before it reaches a balance, so Redis never holds a number `NUMERIC(18, 6)` would round

## Commands
- `just check` -- ruff format + ruff check + mypy + layer guard (full project)
- `just test` -- integration tests (testcontainers, requires Docker)
- `just test-unit` -- unit tests (no Docker needed)
- `just test-all` -- all test sessions
- `just start` / `just stop` / `just delete` / `just logs` -- Docker Compose (PostgreSQL + Redis)
- `just migrate` -- alembic upgrade head
- `just migration "description"` -- autogenerate migration (needs a reachable PostgreSQL from `.env`)
- `just flusher` -- run the write-behind flusher process

## Git — worktree workflow
All commits made during a worktree session (code, fixes, docs, knowledge updates) go on the **feature branch**. All git operations stay inside the worktree — never use `git -C` to operate on the parent repo.

End-of-session: push the feature branch with the translated name, then ask the user to merge it into `main`:
```bash
LOCAL=$(git branch --show-current)
REMOTE=$(echo "$LOCAL" | sed 's/^worktree-//; s/+/\//g')
git push -u origin "$LOCAL:$REMOTE"
```

Hard rules — no exceptions:
- Never force-push `main`
- Never cherry-pick commits between branches
- Never push the raw `worktree-*` branch name to origin

## Rules (`.claude/rules/`) -- loaded contextually by file path
architecture, entities, repositories, database, dependency-injection, ports-adapters, error-handling, typing, code-style, testing, git

## Skills (`.claude/skills/`)

User-invoked overrides:
- `/impl [small|mid|tuff] <change>` -- forces a specific effort tier; omit the tier to confirm auto-detection
- `/chat <problem>` -- design consultation before committing to an approach; outputs options + trade-offs + recommendation; no code written

Orientation:
- `/help` -- overview of the project, all commands, and how to start
- `/explain <question>` -- quick answer from `.claude/` docs only; no codebase scan; for simple conceptual questions

Planning (use BEFORE writing code):
- `/plan <feature>` -- structured planning with questions, research, detailed component spec
- `/research <topic>` -- investigate codebase before making changes

Knowledge (keep `.claude/` in sync):
- `/update-knowledge [topic]` -- review and update rules, skills, agents after changes

Token compression (caveman -- ported from github.com/JuliusBrussee/caveman):
- `/caveman [lite|full|ultra|wenyan|wenyan-ultra]` -- terse "caveman speak" reply mode; cuts output tokens ~65-75%, keeps technical accuracy. `full` is default. Persistent across turns.
- `/caveman-commit` -- terse Conventional-Commit message (subject <=50 chars, body only when "why" non-obvious)
- `/caveman-review` -- one-line-per-finding PR/code-review comments
- `/caveman-compress <file>` -- rewrite a prose `.md`/memory file into caveman prose to save input tokens; backs up original to `<file>.original.md` (gitignored)
- `/caveman-help` -- one-shot reference card for all caveman modes/commands
- `/caveman-stats` -- real session token usage + estimated savings (computed by the mode-tracker hook, not the model)
- State is project-local at `.claude/.caveman-active` (history + statusline-suffix alongside it; all gitignored). The hooks resolve it from their own location (`__dirname/..`); override with `CAVEMAN_STATE_DIR`. So caveman on/off + stats are per-project, not machine-wide. Deactivate with "stop caveman" / "normal mode". Persistence + stats wired via `just caveman-activate` (SessionStart) and `just caveman-track` (UserPromptSubmit) in `settings.json`; statusline badge via `.claude/hooks/caveman-statusline.sh`. Make it opt-in with `~/.config/caveman/config.json` -> `{"defaultMode":"off"}` or `CAVEMAN_DEFAULT_MODE=off`. Node >=18 required for the hooks.

Implementation (use DURING coding):
- `/add-entity <name> <fields>` -- domain entity + hand-written migration
- `/add-repository <entity>` -- protocol + impl + gateway wire
- `/add-port <category> <name>` -- protocol + adapter + DI wire
- `/add-migration <description>` -- Alembic migration (autogenerate or hand-written)
- `/add-service <domain> <name>` -- application service
- `/add-test <domain> <scenario>` -- integration test for a service scenario

Test feedback loop (use AFTER coding):
- `/run-tests <domain | path | category> [pattern]` -- scoped pytest run, faster than `just test`
- `/debug-failing-test <test-path>` -- decision-tree diagnosis using project test recipes

## Agents (`.claude/agents/`)

Quality gates (enforce the workflow):
- **plan-reviewer** -- validates plan completeness. Invoke only when the user explicitly asks for a plan review.
- **implementation-verifier** -- validates code matches plan after implementation. MUST pass.

Review (catch issues):
- **architecture-reviewer** -- audits layer violations, import direction, pattern adherence, Lua conventions
- **code-reviewer** -- quality, money-safety, typing, pattern compliance review
- **migration-reviewer** -- audits Alembic migrations for data loss, NOT NULL footguns, missing indexes, locking, entity drift

Knowledge (keep docs accurate):
- **knowledge-maintainer** -- audits `.claude/` against codebase, finds stale/missing docs

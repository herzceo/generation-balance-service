# Implementation plan: generations and user balances

Assignment: `TASK.md`. This plan is the single source of truth for the implementer. Every file,
signature and algorithm below is normative; deviations must be justified in `docs/DECISIONS.md`.

## 1. Summary

Redis is the hot authority for user balances while the system runs. PostgreSQL is the durable
store, written behind by a background flusher in batches. A generation is a reservation
(atomic compare-and-set in a Lua script) followed by a provider call outside any lock, followed
by a settle or refund. Redelivery of a `client_request_id` is deduplicated by a per-request Redis
record created in the same atomic step as the debit. All money arithmetic is Python `Decimal`;
Lua only compares a `version` counter and writes strings.

The given module (`TASK.md` lines 16–329) is committed byte-identical as
`backend/domain/generation.py`, excluded from ruff, and covered by a mypy `ignore_errors`
override (strict mypy rejects `order = ("paid",)` being reassigned a 3-tuple).

## 2. Target layout

```
backend/
├── domain/
│   ├── generation.py                 given module, byte-identical (TASK.md 16–329)
│   ├── entities/{__init__.py, balance.py, base/{__init__.py, base.py}}
│   └── repos/{__init__.py, balance.py, gateway.py}
├── app/
│   ├── errors.py                     DetailedError hierarchy + billing errors
│   ├── billing/{__init__.py, config.py, balance_loader.py, generation.py, balance.py, flusher.py}
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
├── __init__.py
├── unit/{__init__.py, internal/{__init__.py, test_option.py}, billing/{__init__.py, test_balance_snapshot.py}}
└── integration/
    ├── __init__.py, conftest.py, test_migrations.py
    └── billing/{__init__.py, conftest.py, ioc.py, helpers.py,
                 test_single.py, test_parallel.py, test_redelivery.py, test_provider_errors.py,
                 test_top_up.py, test_cold_cache.py, test_pg_budget.py, test_convergence.py,
                 test_multiprocess.py}
docs/{PLAN.md, DECISIONS.md}
```

Layer rules are unchanged: `domain` → `domain, internal`; `app` → `app, domain, internal`; `infra` →
`infra, domain, backend.app.shared.*, internal`; `entry`/`main` → anything.

## 3. Delete list (template code not needed)

Do this in one `rm -rf` shell command (the `just check` PostToolUse hook fails non-blockingly
while the tree is inconsistent; fewer edits = less noise), then rebuild the `__init__.py` files.

- `backend/app/events/`, `backend/app/rest/`, `backend/app/shared/events/`, `backend/app/shared/handlers/`,
  `backend/app/shared/db/dbus.py`, `backend/app/shared/db/query_services/`,
  `backend/app/shared/ports/{auth,llm,outreach,security,storage}/`
- `backend/domain/entities/{asset,audit_log,identity,notification,notification_interaction,permission,profile,rbac,role,session,tenant,user,user_email}.py`,
  `backend/domain/entities/queue/`, `backend/domain/entities/base/mixins/`, `backend/domain/enums.py`
- `backend/domain/repos/{asset,audit_log,base,identity,notification,permission,profile,role,session,tenant,user,user_email}.py`,
  `backend/domain/repos/queue/`
- `backend/entry/queue/`, `backend/entry/rest/`
- `backend/infra/database/object/`, `backend/infra/database/psql/dbus/`, `backend/infra/database/psql/queries/`,
  `backend/infra/database/psql/repos/{asset,audit_log,base,identity,notification,permission,profile,role,session,tenant,user,user_email}.py`,
  `backend/infra/database/psql/repos/queue/`, all four `versions/2026_*.py` migrations,
  `backend/infra/database/redis/client.py` (replaced), `backend/infra/database/redis/adapters/{config,login_code,oauth_setup_store,one_time_token,rate_limiter,verification_code}.py`,
  `backend/infra/external/`, `backend/infra/security/`
- `backend/internal/result.py`, `backend/internal/di/`, `backend/internal/cls/`
- `tests/unit/internal/test_result.py`, `tests/integration/{adapters,api,events,mocks}/`
- `Dockerfile`, `docker-compose.prod.yaml`, `monitoring/`, `.claude/frontend.example.json`, `.claude/hooks/pr-watcher.py`
- `.claude/rules/{controllers,dtos,handlers,auth,events,external-services,monitoring}.md`
- `.claude/skills/{add-endpoint,add-handler,add-event,add-http-client,front,task,specialize}/`
- `.claude/agents/{feature-implementer,domain-designer}.md`

## 4. Components (implementation order)

### 4.1 Given module — `backend/domain/generation.py`

```bash
sed -n '16,329p' TASK.md > backend/domain/generation.py
diff <(sed -n '16,329p' TASK.md) backend/domain/generation.py && echo IDENTICAL
```

`pyproject.toml`:

```toml
[tool.ruff]
exclude = [".venv", ".claude", "backend/domain/generation.py"]

[[tool.mypy.overrides]]
module = "backend.domain.generation"
ignore_errors = true

[tool.coverage.run]
omit = ["backend/domain/generation.py"]
```

Never edit the file. Every later step must keep `diff` empty.

### 4.2 Entity — `backend/domain/entities/balance.py`

```python
class Balance(Base):
    user_id: Mapped[UUID] = mapped_column(SQL_UUID(as_uuid=True), primary_key=True)
    free_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, server_default=text("0"))
    bonus_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, server_default=text("0"))
    paid_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, server_default=text("0"))
    free_requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

No mixins (natural key, upsert-only writes). `entities/base/__init__.py` exports `Base` only;
`entities/__init__.py` exports `Base`, `Balance`. Table name auto-derives to `balance`.

### 4.3 Migration — `versions/<date>_<rev>_init_balance.py`

Hand-written, `down_revision = None`, `op.create_table("balance", ...)` with
`sa.PrimaryKeyConstraint("user_id", name=op.f("pk_balance"))`, column types exactly as the entity
(`sa.Numeric(18, 6)`, `sa.Integer()`, `sa.BigInteger()`, `sa.DateTime(timezone=True)`, server
defaults `sa.text("0")` / `sa.text("now()")`). `downgrade()` drops the table. Use the existing
`script.py.mako` header format. `tests/integration/test_migrations.py` guards drift (§6).

### 4.4 Repository

`backend/domain/repos/balance.py`:

```python
class BalanceRepo(Protocol):
    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> Option[Balance]: ...
    @abstractmethod
    async def upsert_many(self, balances: list[Balance]) -> None: ...
```

`backend/domain/repos/gateway.py`: `RepoGateway(Protocol)` with a single `balance` property.

`backend/infra/database/psql/repos/balance.py`: `@final class ImplBalanceRepo(BalanceRepo)`,
`__slots__ = ("_session",)`, constructor takes `AsyncSession`. `get_by_user_id` = `select(Balance).where(...)`
→ `Option(result.scalar_one_or_none())`. `upsert_many`: return early on empty list; sort rows by
`user_id` (deadlock avoidance); one statement:

```python
rows = [b.to_builtins() for b in sorted(balances, key=lambda b: b.user_id)]
stmt = insert(Balance).values(rows)
stmt = stmt.on_conflict_do_update(
    index_elements=[Balance.user_id],
    set_={
        "free_usd": stmt.excluded.free_usd, "bonus_usd": stmt.excluded.bonus_usd,
        "paid_usd": stmt.excluded.paid_usd, "free_requests": stmt.excluded.free_requests,
        "version": stmt.excluded.version, "updated_at": func.now(),
    },
    where=stmt.excluded.version > Balance.version,
)
await self._session.execute(stmt)
```

`repos/gateway.py`: `@final ImplRepoGateway` with `@cached_property balance`. `CRUDSupported`/
`ImplCRUDSupported`/`BaseRepo` are deleted (the entity has no `id`; nothing else uses them).

### 4.5 Database — `backend/app/shared/db/database.py`, `backend/infra/database/psql/database.py`

`Database` Protocol keeps `gateway`, `commit`, `rollback`, `flush`, `__aenter__`, `__aexit__`;
the `dbus` property is removed. `ImplDatabase` drops `_dbus`/`ImplDBus`, gains `@final`. Engine
and sessionmaker factories in `engine.py` unchanged (`expire_on_commit=False, autoflush=False`).

### 4.6 Ports — `backend/app/shared/ports/billing/`

`generation_provider.py`:

```python
class GenerationProvider(Protocol):
    @abstractmethod
    async def generate(self, request: GenerationRequest, *, authorized_cost_usd: Decimal) -> GenerationResult: ...
```

`FakeGenerationProvider` satisfies it structurally (verified under strict mypy); no wrapper.

`balance_store.py` — value types and the port:

```python
class BalanceSnapshot(StructDTO, kw_only=True):      # NOT frozen: BalanceView needs settable attrs
    free_usd: Decimal
    bonus_usd: Decimal
    paid_usd: Decimal
    free_requests: int
    version: int

    @classmethod
    def zero(cls) -> BalanceSnapshot: ...            # all zeros, version 0
    def apply_plan(self, plan: DebitPlan) -> BalanceSnapshot: ...      # subtract, version unchanged
    def refund_plan(self, plan: DebitPlan) -> BalanceSnapshot: ...     # add back
    def apply_top_up(self, top_up: BalanceTopUp) -> BalanceSnapshot: ...

class GenerationStatus(StrEnum): RUNNING, DONE, FAILED

class GenerationRecord(StructDTO, kw_only=True):
    status: GenerationStatus
    user_id: UUID
    dialog_id: UUID
    model_name: str
    plan: DebitPlan
    authorized_cost_usd: Decimal
    started_at: float
    content: str | None = None
    billed_cost_usd: Decimal | None = None
    error: str | None = None

@dataclass(frozen=True, slots=True) class Reserved: version: int
@dataclass(frozen=True, slots=True) class Conflict: current: BalanceSnapshot
@dataclass(frozen=True, slots=True) class Missing: ...
@dataclass(frozen=True, slots=True) class Duplicate: record: GenerationRecord
@dataclass(frozen=True, slots=True) class Applied: ...
@dataclass(frozen=True, slots=True) class Stale: ...
type ReserveOutcome = Reserved | Conflict | Missing | Duplicate
type RefundOutcome = Applied | Conflict | Stale
type TopUpOutcome = Applied | Conflict | Missing | Duplicate   # Duplicate.record unused → use a separate `AlreadyApplied` marker

class BalanceStore(Protocol):
    async def load(self, user_id: UUID) -> Option[BalanceSnapshot]: ...
    async def load_many(self, user_ids: Sequence[UUID]) -> dict[UUID, BalanceSnapshot]: ...
    async def seed_if_absent(self, user_id: UUID, snapshot: BalanceSnapshot) -> bool: ...
    async def try_acquire_load_lock(self, user_id: UUID) -> bool: ...
    async def release_load_lock(self, user_id: UUID) -> None: ...
    async def reserve(self, *, request: GenerationRequest, expected_version: int, new: BalanceSnapshot,
                      plan: DebitPlan, authorized_cost_usd: Decimal, started_at: float) -> ReserveOutcome: ...
    async def get_generation(self, client_request_id: UUID) -> Option[GenerationRecord]: ...
    async def settle(self, client_request_id: UUID, *, content: str, billed_cost_usd: Decimal) -> bool: ...
    async def refund(self, *, user_id: UUID, client_request_id: UUID, expected_version: int,
                     new: BalanceSnapshot, error: str) -> RefundOutcome: ...
    async def top_up(self, *, operation_id: UUID, user_id: UUID, expected_version: int,
                     new: BalanceSnapshot) -> TopUpOutcome: ...
    async def dirty_users(self, limit: int) -> list[UUID]: ...
    async def clear_dirty(self, flushed: Sequence[tuple[UUID, int]]) -> None: ...
```

Define `TopUpOutcome = Applied | Conflict | Missing | AlreadyApplied` (separate empty marker
class) rather than reusing `Duplicate`. Serialisation helpers (module-level, pure):
`money_to_str(d) -> str` = `format(d, "f")`, `str_to_money(s) -> Decimal`.

### 4.7 Config — `backend/app/billing/config.py`

```python
class BillingConfig(StructDTO):
    GENERATION_RECORD_TTL_SECONDS: int = 86400
    TOP_UP_RECORD_TTL_SECONDS: int = 86400
    RUNNING_WAIT_TIMEOUT_SECONDS: float = 30.0
    RUNNING_POLL_INTERVAL_SECONDS: float = 0.02
    RESERVE_MAX_ATTEMPTS: int = 100
    LOAD_LOCK_TTL_MS: int = 5000
    LOAD_WAIT_TIMEOUT_SECONDS: float = 5.0
    FLUSH_INTERVAL_SECONDS: float = 1.0
    FLUSH_BATCH_SIZE: int = 500
```

Loaded via `load_from_env(BillingConfig)` in the CLI; provided as an APP singleton.

### 4.8 Errors — `backend/app/errors.py`

Keep `ApplicationError`, `DetailedError`, `NotFoundError`, `InvalidInputError`, `ConflictError`.
Add:

```python
class GenerationFailedError(DetailedError):      _default_code = "generation_failed"
class GenerationInProgressError(DetailedError):  _default_code = "generation_in_progress"
class BalanceContentionError(ConflictError):     _default_code = "balance_contention"   # CAS/load retries exhausted
```

Access errors from the given module (`InsufficientBalanceError`, `FreeRequestsExhaustedError`,
`ConditionalFreeAccessDeniedError`) propagate unchanged.

### 4.9 Redis adapter — `backend/infra/database/redis/`

`config.py` unchanged. `client.py`: `create_redis_client(config: RedisConfig) -> Redis` =
`redis.asyncio.from_url(config.url, decode_responses=True)`. `scripts.py`: Lua sources as module
constants. `adapters/balance_store.py`: `@final class ImplRedisBalanceStore(BalanceStore)`,
constructor `(redis: Redis, config: BillingConfig)`, registers scripts with
`redis.register_script(...)` once (EVALSHA with automatic EVAL fallback).

Key layout:

| Key | Type | Content |
|---|---|---|
| `bal:{user_id}` | HASH | `free_usd bonus_usd paid_usd free_requests version` (strings) — no TTL |
| `bal:dirty` | SET | user ids with unflushed changes |
| `bal:load:{user_id}` | STRING | cold-load lock, `SET NX PX LOAD_LOCK_TTL_MS` |
| `gen:{client_request_id}` | HASH | generation record, TTL `GENERATION_RECORD_TTL_SECONDS` for every status |
| `topup:{operation_id}` | STRING | idempotency marker = `user_id`, TTL `TOP_UP_RECORD_TTL_SECONDS` |

Lua return convention: array whose first element is a tag; money only as strings; `''` sentinels,
never `nil` inside tables. All keys through `KEYS`, all values/TTLs/timestamps through `ARGV`
(no `TIME`, no randomness). Scripts:

```lua
-- RESERVE  KEYS: bal, gen, dirty
-- ARGV: 1 expected_version, 2 free, 3 bonus, 4 paid, 5 free_requests (new values),
--       6 user_id, 7 dialog_id, 8 model_name, 9 plan_free, 10 plan_bonus, 11 plan_paid,
--       12 plan_free_requests, 13 authorized_cost, 14 started_at, 15 ttl
if redis.call('EXISTS', KEYS[2]) == 1 then
  local r = redis.call('HMGET', KEYS[2], 'status','user_id','dialog_id','model_name','plan_free_usd',
    'plan_bonus_usd','plan_paid_usd','plan_free_requests','authorized_cost_usd','started_at',
    'content','billed_cost_usd','error')
  for i = 1, #r do if not r[i] then r[i] = '' end end
  return {'DUP', unpack(r)}
end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'MISSING'} end
local c = redis.call('HMGET', KEYS[1], 'version','free_usd','bonus_usd','paid_usd','free_requests')
if c[1] ~= ARGV[1] then return {'CONFLICT', c[2], c[3], c[4], c[5], c[1]} end
local v = redis.call('HINCRBY', KEYS[1], 'version', 1)
redis.call('HSET', KEYS[1], 'free_usd', ARGV[2], 'bonus_usd', ARGV[3], 'paid_usd', ARGV[4], 'free_requests', ARGV[5])
redis.call('HSET', KEYS[2], 'status','running', 'user_id',ARGV[6], 'dialog_id',ARGV[7], 'model_name',ARGV[8],
  'plan_free_usd',ARGV[9], 'plan_bonus_usd',ARGV[10], 'plan_paid_usd',ARGV[11], 'plan_free_requests',ARGV[12],
  'authorized_cost_usd',ARGV[13], 'started_at',ARGV[14])
redis.call('EXPIRE', KEYS[2], ARGV[15])
redis.call('SADD', KEYS[3], ARGV[6])
return {'OK', tostring(v)}
```

```lua
-- SETTLE  KEYS: gen   ARGV: 1 content, 2 billed_cost, 3 ttl
if redis.call('HGET', KEYS[1], 'status') ~= 'running' then return 0 end
redis.call('HSET', KEYS[1], 'status','done', 'content',ARGV[1], 'billed_cost_usd',ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
```

```lua
-- REFUND  KEYS: bal, gen, dirty   ARGV: 1 expected_version, 2..5 new free/bonus/paid/fr, 6 user_id, 7 error, 8 ttl
if redis.call('HGET', KEYS[2], 'status') ~= 'running' then return {'STALE'} end
local c = redis.call('HMGET', KEYS[1], 'version','free_usd','bonus_usd','paid_usd','free_requests')
if c[1] ~= ARGV[1] then return {'CONFLICT', c[2], c[3], c[4], c[5], c[1]} end
redis.call('HINCRBY', KEYS[1], 'version', 1)
redis.call('HSET', KEYS[1], 'free_usd',ARGV[2], 'bonus_usd',ARGV[3], 'paid_usd',ARGV[4], 'free_requests',ARGV[5])
redis.call('HSET', KEYS[2], 'status','failed', 'error',ARGV[7])
redis.call('EXPIRE', KEYS[2], ARGV[8])
redis.call('SADD', KEYS[3], ARGV[6])
return {'OK'}
```

```lua
-- TOP_UP  KEYS: bal, marker, dirty   ARGV: 1 expected_version, 2..5 new free/bonus/paid/fr, 6 user_id, 7 ttl
if redis.call('EXISTS', KEYS[2]) == 1 then return {'DUP'} end
if redis.call('EXISTS', KEYS[1]) == 0 then return {'MISSING'} end
local c = redis.call('HMGET', KEYS[1], 'version','free_usd','bonus_usd','paid_usd','free_requests')
if c[1] ~= ARGV[1] then return {'CONFLICT', c[2], c[3], c[4], c[5], c[1]} end
redis.call('HINCRBY', KEYS[1], 'version', 1)
redis.call('HSET', KEYS[1], 'free_usd',ARGV[2], 'bonus_usd',ARGV[3], 'paid_usd',ARGV[4], 'free_requests',ARGV[5])
redis.call('SET', KEYS[2], ARGV[6], 'EX', ARGV[7])
redis.call('SADD', KEYS[3], ARGV[6])
return {'OK'}
```

```lua
-- SEED_IF_ABSENT  KEYS: bal   ARGV: 1 free, 2 bonus, 3 paid, 4 fr, 5 version
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'free_usd',ARGV[1], 'bonus_usd',ARGV[2], 'paid_usd',ARGV[3], 'free_requests',ARGV[4], 'version',ARGV[5])
return 1
```

```lua
-- CLEAR_DIRTY  KEYS: bal, dirty   ARGV: 1 version, 2 user_id
if redis.call('EXISTS', KEYS[1]) == 0 or redis.call('HGET', KEYS[1], 'version') == ARGV[1] then
  redis.call('SREM', KEYS[2], ARGV[2]); return 1
end
return 0
```

Adapter method mapping: `load` = `HGETALL`; `load_many` = pipelined `HGETALL`; `dirty_users` =
`SRANDMEMBER bal:dirty limit`; `clear_dirty` = pipelined CLEAR_DIRITY; `try_acquire_load_lock` =
`SET key 1 NX PX ttl`; `release_load_lock` = `DEL`; `get_generation` = `HGETALL gen:*` → record or
`Option(None)`. Parsing lives in private helpers (`_snapshot_from_fields`, `_record_from_fields`).

### 4.10 Services — `backend/app/billing/`

All `@dataclass`, REQUEST-scoped, dependencies typed to Protocols.

`balance_loader.py` — `BalanceLoader(store: BalanceStore, db: Database, config: BillingConfig)`:

```
async def ensure_loaded(user_id) -> BalanceSnapshot:
    deadline = monotonic() + LOAD_WAIT_TIMEOUT_SECONDS
    while True:
        if (snap := (await store.load(user_id)).value) is not None: return snap
        if await store.try_acquire_load_lock(user_id):
            try:
                if (snap := (await store.load(user_id)).value) is not None: return snap   # EXISTS after lock
                async with db:
                    row = (await db.gateway.balance.get_by_user_id(user_id)).value        # the only PG read
                    await db.commit()
                seed = BalanceSnapshot(from row) if row else BalanceSnapshot.zero()
                await store.seed_if_absent(user_id, seed)
            finally:
                await store.release_load_lock(user_id)
            continue
        if monotonic() > deadline: raise BalanceContentionError(message="balance load timed out")
        await asyncio.sleep(0.01 + random.uniform(0, 0.005))
```

`generation.py` — `GenerationService(store, provider: GenerationProvider, loader: BalanceLoader,
config: BillingConfig)`, `policy: DebitPolicyService = field(default_factory=DebitPolicyService)`:

```
async def execute_generation(request) -> GenerationResult:
    if request.model_name not in MODELS: raise InvalidInputError(message=f"unknown model {request.model_name!r}")
    snapshot = await loader.ensure_loaded(request.user_id)
    for attempt in range(RESERVE_MAX_ATTEMPTS):
        authorization = policy.authorize(request, snapshot)         # access errors propagate
        plan = authorization.debit_plan
        outcome = await store.reserve(request=request, expected_version=snapshot.version,
                                      new=snapshot.apply_plan(plan), plan=plan,
                                      authorized_cost_usd=authorization.estimated_cost_usd, started_at=time())
        match outcome:
            case Reserved(): break
            case Conflict(current): snapshot = current; if attempt >= 5: await sleep(uniform(0, 0.002)); continue
            case Missing(): snapshot = await loader.ensure_loaded(request.user_id); continue
            case Duplicate(record):
                resolved = await self._resolve_duplicate(request, record)   # None → record vanished, retry
                if resolved is not None: return resolved
                snapshot = await loader.ensure_loaded(request.user_id); continue
    else: raise BalanceContentionError(message="reservation retries exhausted")
    try:
        result = await provider.generate(request, authorized_cost_usd=authorization.estimated_cost_usd)
    except BaseException as exc:
        await self._refund(request, plan, error=str(exc) or type(exc).__name__)
        if isinstance(exc, Exception): raise GenerationFailedError(message=str(exc)) from exc
        raise
    settled = await store.settle(request.client_request_id, content=result.content, billed_cost_usd=result.billed_cost_usd)
    if not settled: log.warning(...)          # record no longer running (expired); charge stands, result returned
    return result

async def _resolve_duplicate(request, record) -> GenerationResult | None:
    if (record.user_id, record.dialog_id, record.model_name) != (request.user_id, request.dialog_id, request.model_name):
        raise InvalidInputError(message="client_request_id reused with a different payload")
    deadline = monotonic() + RUNNING_WAIT_TIMEOUT_SECONDS
    while True:
        match record.status:
            case DONE:   return GenerationResult(request.client_request_id, record.content, record.billed_cost_usd)
            case FAILED: raise GenerationFailedError(message=record.error)
        if monotonic() > deadline: raise GenerationInProgressError(message="generation still running")
        await asyncio.sleep(RUNNING_POLL_INTERVAL_SECONDS)
        fresh = (await store.get_generation(request.client_request_id)).value
        if fresh is None: return None
        record = fresh

async def _refund(request, plan, *, error) -> None:
    snapshot = await loader.ensure_loaded(request.user_id)
    for _ in range(RESERVE_MAX_ATTEMPTS):
        outcome = await store.refund(user_id=..., client_request_id=..., expected_version=snapshot.version,
                                     new=snapshot.refund_plan(plan), error=error)
        match outcome:
            case Applied() | Stale(): return
            case Conflict(current): snapshot = current
    raise BalanceContentionError(message="refund retries exhausted")
```

`balance.py` — `BalanceService(store, loader, config)`:

```
async def apply_top_up(command: BalanceTopUp) -> None:
    validate: paid_usd, bonus_usd >= 0 with <= 6 decimal places (amount == amount.quantize(Decimal("0.000001"))),
              free_requests >= 0; else InvalidInputError
    snapshot = await loader.ensure_loaded(command.user_id)
    for _ in range(RESERVE_MAX_ATTEMPTS):
        outcome = await store.top_up(operation_id=..., user_id=..., expected_version=snapshot.version,
                                     new=snapshot.apply_top_up(command))
        match outcome:
            case Applied() | AlreadyApplied(): return
            case Conflict(current): snapshot = current
            case Missing(): snapshot = await loader.ensure_loaded(command.user_id)
    raise BalanceContentionError(message="top-up retries exhausted")

async def get_balance(user_id) -> BalanceSnapshot:  return await loader.ensure_loaded(user_id)
```

`flusher.py` — `BalanceFlusher(store, db, config)`:

```
async def flush_once() -> int:
    users = await store.dirty_users(FLUSH_BATCH_SIZE)
    if not users: return 0
    snapshots = await store.load_many(users)                       # missing keys are skipped
    rows = [Balance(user_id=u, free_usd=s.free_usd, ..., version=s.version) for u, s in snapshots.items()]
    if rows:
        async with db:
            await db.gateway.balance.upsert_many(rows)           # exactly one statement
            await db.commit()
    await store.clear_dirty([(u, snapshots[u].version) if u in snapshots else (u, -1) for u in users])
    return len(rows)

async def run() -> None:
    try:
        while True:
            flushed = await flush_once()
            if flushed < FLUSH_BATCH_SIZE: await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        await flush_once()
        raise
```

`clear_dirty` with version `-1` for users whose hash is gone: CLEAR_DIRTY already removes when the
hash does not exist, so the version argument is irrelevant in that branch.

### 4.11 Composition root — `backend/entry/ioc.py`, `backend/entry/flusher.py`, `backend/main/cli.py`

```python
def create_container(*, engine: AsyncEngine, redis: Redis, billing_config: BillingConfig,
                     provider: GenerationProvider | None = None) -> AsyncContainer
```

Providers: APP — `AsyncEngine`, `async_sessionmaker[AsyncSession]`, `Redis`, `BillingConfig`,
`ImplRedisBalanceStore → BalanceStore`, `provider → GenerationProvider` (only when given);
REQUEST — `ImplDatabase → Database` (async generator closing the session), `BalanceLoader`,
`GenerationService`, `BalanceService`, `BalanceFlusher`. Engine/redis lifetimes belong to the
caller (CLI or test fixture).

`entry/flusher.py`: `run_flusher(db_config, redis_config, billing_config)` → `asyncio.run(_run(...))`:
build engine + redis, container, `async with container() as c: flusher = await c.get(BalanceFlusher)`,
install SIGINT/SIGTERM handlers that cancel the run task, `await task` (final flush happens inside
`run()`), then `container.close()`, `redis.aclose()`, `engine.dispose()`. Logging via stdlib.

`main/cli.py`: subcommands `alembic` (unchanged) and `flusher`; `cmd_run_flusher` loads
`DatabaseConfig`, `RedisConfig`, `BillingConfig` via `load_from_env`.

### 4.12 `backend/internal/dto/struct.py`

Drop the `uuid_utils` enc/dec hooks (stdlib `uuid.UUID` is native to msgspec); `uuid-utils` leaves
the dependency list. Keep `to_builtins`/`from_builtins`/`from_object`.

## 5. Project configuration

`pyproject.toml`: `requires-python = ">=3.12,<3.14"`; runtime deps `alembic, dishka, greenlet,
hiredis, msgspec, psycopg[binary,pool], python-dotenv, redis, sqlalchemy`; dev deps `backend,
mypy, nox, pre-commit, pytest, pytest-asyncio, pytest-cov, ruff, testcontainers[postgres,redis]`;
ruff exclude + mypy override + coverage omit from §4.1; drop per-file-ignores for deleted paths
(`backend/entry/rest/common/middlewares/**`, `backend/infra/external/http/sessions/**`); keep the
`tests/**` and `backend/domain/entities/**` ignores. Then `uv lock && uv sync --group=dev`.

`justfile`: keep `init check status test test-unit test-all start stop delete logs migrate
migration caveman-activate caveman-track notify stop-reminder stop-reflect plan-guard
workflow-reminder`; delete `run test-adapters prod-* guard-paths pr-watcher`; add
`flusher: uv run backend flusher`; `start` defaults to all services; `delete` uses
`docker compose -f {{compose_file}} down -v`.

`noxfile.py`: sessions `integration` and `unit` only. `.github/workflows`: remove the hadolint
step (Dockerfile deleted); everything else unchanged. `.pre-commit-config.yaml`: remove hadolint.
`docker-compose.local.yaml`: services `database` and `redis` only, container/volume prefix
`generation-balance.local.*`, redis keeps `--appendonly yes`. `.env.example`: `POSTGRES_*`
(incl. `POSTGRES_MAX_CONNECTIONS`), `REDIS_*`, all `BillingConfig` keys with defaults.

## 6. Tests

Conventions: `test_{action}_{condition}`; fresh `uuid4()` user per test; `FLUSHDB` per test;
`wait_until(predicate, timeout, interval)` helper instead of sleeps; assert exact PG statement
counts; assert `max_active_calls >= 2` for parallelism (print the actual value).

`tests/integration/conftest.py`: session `postgres_url` (alembic upgrade head, `postgres:16-alpine`),
`redis_url` (`redis:7-alpine`), `manager` (`multiprocessing.Manager()`, `shutdown()` on teardown).

`tests/integration/billing/conftest.py` (function scope): `shared_state`, `provider`
(`FakeGenerationProvider(shared_state)`), `engine` (NullPool, disposed on teardown), `redis`
(`from_url`, `flushdb()` before yield, `aclose()` after), `billing_config` (fast defaults:
`RUNNING_POLL_INTERVAL_SECONDS=0.01`, `FLUSH_INTERVAL_SECONDS=0.05`), `container`
(`create_container(engine=..., redis=..., billing_config=..., provider=...)`, closed on teardown),
`store`, `pg_counter` (listener on `engine.sync_engine` `before_cursor_execute`; `.count`,
`.statements`, `.reset()`). `helpers.py`: `run_generation(container, request)`,
`run_top_up(container, command)`, `seed_pg_balance(container, user_id, **fields)` (writes a
`Balance` row through the repo; caller resets `pg_counter` after), `read_pg_balance`,
`read_redis_balance`, `basic_request(user_id, **overrides)`, `wait_until`.

| File | Tests |
|---|---|
| `test_single.py` | basic with paid debits 0.08 paid; basic without paid debits 0.05 free + 1 free_request; mixed buckets takes free first then `InsufficientBalanceError`; parametrized policy table (premium 0.20/0.19; conditional paid≥0.12 → PAID_ONLY; eligible paid<0.12 → PAID_THEN_FREE_THEN_BONUS 0.07 + free_request; not eligible → `ConditionalFreeAccessDeniedError`; `FreeRequestsExhaustedError`; `InsufficientBalanceError`); unknown model → `InvalidInputError`, provider untouched; denied request leaves balance and provider untouched |
| `test_parallel.py` | premium paid=1.00 ×30 → exactly 5 ok / 25 `InsufficientBalanceError`, paid == 0, `calls` 5 keys each 1, `max_active_calls >= 2`; basic free=0.25 fr=3 ×30 → 3 ok / 27 `FreeRequestsExhaustedError`; basic paid=10.00 ×30 distinct dialogs → 30 ok, paid 7.60, Σ billed 2.40; every test asserts non-negative buckets and `initial − final == Σ billed` |
| `test_redelivery.py` | in-flight (delay 0.5; start task; `wait_until(active_calls >= 1)`; duplicate call) → equal results, `calls[id] == 1`, debited once; after completion → cached, `calls[id] == 1`; after failure → `GenerationFailedError` again, `calls[id] == 1`, balance == seed; payload mismatch → `InvalidInputError`; waiter timeout (reserve via `store.reserve` directly, `RUNNING_WAIT_TIMEOUT_SECONDS=0.2`) → `GenerationInProgressError`, provider never called |
| `test_provider_errors.py` | fail scenario → `GenerationFailedError`, balance == seed, record status `failed`, `calls[id] == 1`; mixed 10 premium with 4 failing on paid=1.00 → 4 failed, 5 ok, 1 `InsufficientBalanceError`, paid == 0; failures do not block other dialogs (`max_active_calls >= 2`) |
| `test_top_up.py` | increments all buckets and version; same `operation_id` twice (sequential and concurrent) applied once; negative amount / >6 decimals → `InvalidInputError`, Redis untouched; concurrent 30 premium + top-up 1.00 on paid=0.20 → invariant `paid == 1.20 − 0.20·ok`, `1 <= ok <= 6`; top-up on cold cache loads PG first (exactly 1 SELECT) |
| `test_cold_cache.py` | PG row seeded, Redis empty → success, Redis == PG − debit, PG unchanged until flush, 1 SELECT; 30 concurrent cold → exactly 1 SELECT; user without PG row → zeros seeded, `FreeRequestsExhaustedError`, 1 SELECT; then top-up + flush creates the row |
| `test_pg_budget.py` | 30 gens cold → `count == 1` before flush, `== 2` after (`statements[0]` starts with `SELECT`, `[1]` with `INSERT`); warm batch of 30 → `+1`; flush with nothing dirty → `0`; numbers printed for README |
| `test_convergence.py` | gens + top-ups + one failure, `flush_once()` → PG row equals Redis field-by-field and by version, `bal:dirty` empty, second flush returns 0 with 0 statements; background `run()` task converges (`wait_until`), cancel → final flush lands a last-moment mutation |
| `test_multiprocess.py` | `mp.get_context("spawn").Pool(3).starmap(_run_worker, ...)`, 3×10 premium on paid=1.00, cold Redis → exactly 5 ok / 25 `InsufficientBalanceError` (by class name), `calls` 5 keys each 1, `max_active_calls >= 2`, Redis paid == 0, `sum(child pg counts) == 1`, parent `flush_once()` → PG == Redis; two workers same `client_request_id` (delay 0.5) → identical content, `calls[id] == 1`, debited once |
| `test_migrations.py` | `compare_metadata(MigrationContext.configure(sync_conn), Base.metadata) == []` via `run_sync` |
| `tests/unit/billing/test_balance_snapshot.py` | `apply_plan`/`refund_plan`/`apply_top_up` arithmetic; `money_to_str`/`str_to_money` round trip incl. `Decimal("1E+2")` → `"100"` |

`_run_worker(shared_state, redis_url, postgres_url, user_id: str, requests: list[tuple[str, str, str, bool]],
delay: float) -> dict` is module-level in `test_multiprocess.py`, builds its own engine/redis/
container, runs `asyncio.run(...)`, returns builtins only (`results: list[tuple[id, content|None,
error_name|None, billed|None]]`, `pg_statements: int`).

## 7. README (Russian) and `.claude/` updates

`README.md` sections: что это и стек · запуск (`uv sync`, `.env`, `just start`, `just migrate`,
`just flusher`; тесты `just test`, `just test-unit`, `just check`; troubleshooting
`TESTCONTAINERS_RYUK_DISABLED=true`) · схема PostgreSQL и миграция · архитектура (слои, модуль
задания без изменений, ключи Redis, Lua-скрипты, поток `execute_generation`, `apply_top_up`,
холодная загрузка, flusher) · принятые решения и ограничения · как проверялась корректность
(таблица тест → инвариант) · как проверялось снижение обращений к PostgreSQL (счётчик
`before_cursor_execute`, измеренные числа) · что бы сделал при наличии времени. Style: short
sentences, no em-dashes, no semicolons, concrete numbers.

`.claude/`: rewrite `CLAUDE.md` (stack, layout, patterns: given module, CAS reservation, write-behind,
commands, skills/agents lists), `rules/{architecture,database,repositories,entities,error-handling,
ports-adapters,dependency-injection,code-style,testing}.md`; keep `git.md`, `typing.md`; rewrite skills
`add-entity, add-repository, add-service, add-port, add-migration, add-test, run-tests,
debug-failing-test, impl, plan, research, chat, help, explain, update-knowledge` (paths, no HTTP;
fix the migration path to `backend/infra/database/psql/alembic/`); trim agents
`architecture-reviewer, code-reviewer, implementation-verifier, migration-reviewer, plan-reviewer,
knowledge-maintainer` (drop handler/controller/event sections). Keep `caveman-*` skills and all
remaining hooks; `settings.json` unchanged.

## 8. Execution order

1. `sed` the given module into place; `pyproject.toml` exclusions; verify `diff` empty.
2. Bulk delete (§3) in one command; rebuild `__init__.py` exports; prune `pyproject.toml` deps;
   `uv lock && uv sync --group=dev`.
3. Entity → migration → repo protocol → repo impl → gateways → `Database`/`ImplDatabase` cleanup.
4. Ports (`billing/`) → config → errors → Redis client/scripts/adapter.
5. Services (loader → generation → balance → flusher) → `entry/ioc.py`, `entry/flusher.py`, `main/cli.py`.
6. `just check` green.
7. Tests (conftest/ioc/helpers → unit → single → parallel → redelivery → provider errors → top-up →
   cold cache → pg budget → convergence → multiprocess → migrations); `just test` and `just test-unit` green.
8. justfile / noxfile / compose / `.env.example` / CI / pre-commit / README / `.claude/`.
9. `find backend tests -name '*.py' | git check-ignore --stdin` must print nothing; final
   `diff <(sed -n '16,329p' TASK.md) backend/domain/generation.py` empty; `just check`, `just test-all` green.
10. Review agents (implementation-verifier, architecture-reviewer, code-reviewer, migration-reviewer),
    fix findings, commit on `main`, create the private GitHub repo, push.

## 9. Verification checklist

- [ ] `just check` passes (ruff format, ruff check, mypy strict, layer guard)
- [ ] `just test-unit` and `just test` pass locally (Docker required)
- [ ] `backend/domain/generation.py` byte-identical to `TASK.md` 16–329
- [ ] PG statement counts printed by `test_pg_budget.py` match the README numbers
- [ ] `git check-ignore` reports no source file
- [ ] `.claude/` references no deleted path (grep for `rest/`, `handlers/`, `controllers`, `dbus`, `queue`)

# Decisions, edge cases, assumptions, open items

Companion to `docs/PLAN.md`. Each decision names the alternatives considered and why they lost.

## 1. Decisions

### D1. Redis is the source of truth at run time; PostgreSQL is a write-behind snapshot

Every balance mutation happens in Redis. A flusher copies dirty balances to PostgreSQL in batches
(one multi-row upsert per batch) and clears the dirty flag only if the balance version has not
moved since it was read. PostgreSQL is read exactly once per user per cache lifetime (cold load).

Alternatives: (a) PostgreSQL-first with Redis as a cache — every generation is a PG write, which
the assignment forbids in spirit; (b) delta/ledger outbox replayed into PG — same durability window
(the outbox also lives in Redis), two tables, replay idempotency, ~2× code, gains only an audit
trail. Snapshot write-behind is the smallest design that meets "PG accesses ≪ generations".

### D2. Reservation = optimistic compare-and-set on a per-user `version`, executed in Lua

Flow: `HGETALL` snapshot → `DebitPolicyService.authorize(request, snapshot)` in Python → Lua
`RESERVE` that applies the plan only if `version` is unchanged, bumping it atomically. On conflict
the script returns the current balance so the retry costs one round trip.

Why this and not: (a) a per-user Redis lock (`SET NX PX`) — a lock that expires under a stalled
holder admits two writers unless you also CAS, and a crashed holder stalls every request for the
lock TTL; (b) `WATCH/MULTI` — cannot branch (DUP/MISSING/CONFLICT) inside `MULTI`; (c) porting the
policy into Lua — a second copy of logic the task forbids changing; (d) applying a plan whenever
buckets are still sufficient — never overspends, but the resulting history is not always
explainable by any serial application of the policy (decision variables like `has_paid_balance`
can flip between snapshot and apply), and it needs money comparisons in Lua.

Strict CAS makes the mutation history serial by construction: every applied plan was authorized
against the exact preceding balance state. Invariants: buckets never negative (each plan takes
`min(available, remaining)` from the snapshot it was applied to), never overspend, every executed
generation authorized by the policy.

Contention bound: with N=30 concurrent requests for one user, at most N(N+1)/2 = 465 script
executions (~0.1 ms Redis CPU each); jitter after the 5th conflict; `RESERVE_MAX_ATTEMPTS=100`
then `BalanceContentionError`.

### D3. All money arithmetic in Python `Decimal`; Redis stores plain strings

Serialised with `format(d, "f")` (never `str()`, which can emit `1E+2`). Lua never adds or
compares amounts; it compares only the integer `version` and writes the strings it is given.
`Numeric(18, 6)` in PostgreSQL; top-ups validated to ≤ 6 decimal places so Redis ↔ PG round trips
are exact. Rejected: fixed-point integers in Redis (would allow `HINCRBY` arithmetic in Lua but
introduces a representation conversion on every read/write for no measurable gain).

### D4. Per-request record created in the same Lua step as the debit

`gen:{client_request_id}` is written by `RESERVE` together with the balance change. Redis scripts
are serialised, so at most one caller ever obtains `OK` per record lifetime: no two provider
calls for one id, no double debit. The record stores the plan, so a refund is exact.

### D5. Failed generations are cached (`status=failed`); provider invoked at most once per id

Redelivery of a failed `client_request_id` re-raises `GenerationFailedError` without touching the
provider or the balance. "One operation, one outcome": a new attempt is a new
`client_request_id`. This keeps `provider.calls[id] == 1` in every combination (single, in-flight
duplicate, redelivery after failure). Alternative kept as a one-line switch (`REFUND` deletes the
record instead of marking it failed): redelivery would retry, matching the Stripe convention of
not caching 5xx. Rejected because a redelivering queue would then hammer a real provider and
because the fake provider fails deterministically per id, so a retry cannot succeed anyway.

Access errors (`InsufficientBalanceError`, `FreeRequestsExhaustedError`,
`ConditionalFreeAccessDeniedError`, unknown model) are never cached: they have no side effects and
a top-up between deliveries legitimately turns a denial into a success.

### D6. In-flight redelivery waits by polling the record

`HGETALL gen:{id}` every 20 ms with a 30 s deadline, then `GenerationInProgressError`. Pub/sub,
keyspace notifications and in-process events were rejected as extra machinery for a 100 ms
provider. The waiter never touches the balance.

### D7. The authorization is a ceiling; `settle` reconciles what the provider actually billed

`SETTLE` records the result and, when the provider billed less than the reserved plan, gives the
surplus back in the same Lua step through the usual `version` CAS. The surplus is returned LIFO
along the policy's charge order (`DEFAULT` charges free → bonus → paid, so it refunds paid →
bonus → free); `free_requests` are never returned. A bill above the authorized total is capped:
the user is charged the authorized amount, the raw provider value is kept on the record
(`provider_billed_cost_usd`) and a warning is logged. `GenerationResult.billed_cost_usd` and the
cached redelivery outcome both carry the amount actually charged. `FakeGenerationProvider`
always bills the authorized cost, so the fast path (`expected_version=None`) does not touch the
balance; tests exercise the surplus path with a wrapping provider that bills less or more.

### D8. Refund is a CAS in Python, guarded by `status == running`

The provider failure path recomputes `snapshot + plan` in Python and applies it with the same
version CAS as a reservation; the script refuses if the record is no longer `running`, so a double
refund is impossible. Cancellation (`BaseException`) also refunds, then re-raises.

### D9. Cold load behind a Redis lock, seed only if absent

`MISSING` → `SET bal:load:{user} NX PX 5000` → after winning, re-check `EXISTS bal:{user}` (avoids
the second SELECT when the winner's `DEL` released a loser) → one `SELECT` → `SEED_IF_ABSENT` →
`DEL` lock. Losers sleep ~10 ms with jitter and retry; 5 s deadline. A user without a PG row is
seeded with zeros and `version 0`, not marked dirty; the row materialises on the first real
mutation. Result: 30 concurrent cold requests across 3 processes cost exactly one PostgreSQL
SELECT.

### D10. Dirty-set flushing is version-conditional

`SADD bal:dirty` happens inside every mutating script. The flusher reads members, pipelines
`HGETALL`, writes one `INSERT … ON CONFLICT (user_id) DO UPDATE … WHERE excluded.version >
balance.version` (rows sorted by `user_id` to avoid deadlocks between concurrent flushers), then
clears each flag only if the hash version equals the flushed version. A mutation that lands
between read and clear leaves the user dirty; a crash before the clear re-flushes idempotently.
Strict `>` (not `>=`) also makes a stale pre-restart snapshot harmless after a Redis data loss
and reseed.

### D11. One record TTL for every status; waiter timeout is a separate, shorter value

`running`, `done` and `failed` records all live `GENERATION_RECORD_TTL_SECONDS` (24 h). A short
TTL on `running` would let a still-running debit lose its record and re-enable double charging on
redelivery. Staleness is the reaper's job, not TTL's (see D16).

### D12. Payload drift on the same `client_request_id` is rejected

`DUP` returns `user_id`, `dialog_id`, `model_name`; a redelivery with a different payload raises
`InvalidInputError`. Three string comparisons close the "same id, different model" hole.

### D13. Template pruning and placement

The given module lives in `backend/domain/generation.py` (stdlib-only imports, satisfies the layer
guard), excluded from ruff (`exclude`, because the formatter would rewrite one statement and lint
flags EM101/FURB157/RSE102) and mypy-overridden with `ignore_errors` (strict mypy rejects
`order = ("paid",)` being reassigned a 3-tuple). Everything REST/auth/OAuth/email/S3/queue/
monitoring/external-HTTP is deleted; `CRUDSupported`, entity mixins, `RedisClient`, `Result`,
`GlobalContainer` go with them. `FakeGenerationProvider` satisfies the `GenerationProvider`
Protocol structurally; no adapter. Services stay REQUEST-scoped `@dataclass`es per the template.

### D14. Schema drift guard instead of autogenerate

The single migration is hand-written (no PG reachable at dev time without Docker). An integration
test runs `alembic.autogenerate.compare_metadata` against the migrated testcontainer database and
asserts an empty diff.

### D15. PG statement counting is in-process and exact

A `before_cursor_execute` listener on `engine.sync_engine` counts every application statement and
nothing else (verified: psycopg async + SQLAlchemy 2.0.46 emit no dialect-init, `BEGIN`, `COMMIT`
or ping statements through the cursor). Multi-process workers report their own counts.
`pg_stat_*` views were rejected (lazy stats flush, count transactions not statements).

### D16. Stuck reservations are reaped by the flusher process

`RESERVE` also adds the id to the `gen:inflight` ZSET scored by `started_at`; `SETTLE` and
`REFUND` remove it. Each flusher tick runs `ReservationReaper.reap_once`: ids older than
`REAP_AFTER_SECONDS` (300 s) whose record is still `running` are refunded through the same
`REFUND` CAS (guarded by `status == running`, so a concurrent normal refund cannot double it) and
marked `failed`; ids whose record already finished are simply dropped from the index. A holder
that finishes after the reap gets `STALE` from `SETTLE`, discards its content and raises
`GenerationFailedError`; nothing is charged twice or refunded twice. The reaper never touches
PostgreSQL. Trade-off: a generation that legitimately runs longer than `REAP_AFTER_SECONDS` is
cancelled; the threshold is configuration.

### D17. Transient Redis errors on the money path are retried

`settle`, `refund` and `forget_inflight` retry `redis.exceptions.ConnectionError` /
`TimeoutError` up to `RETRY_ATTEMPTS` (3) with a 50 ms · attempt back-off. The scripts are
idempotent (`status == running` guard, version CAS), so a command whose reply was lost can be
replayed safely. Other exceptions propagate.

### D18. A redelivery keeps its cached outcome even when the balance has since dropped

`authorize` runs before `RESERVE`, so a redelivery of a finished generation could be denied by
the policy when the balance no longer covers the cost. `GenerationAccessError` therefore first
checks for an existing record and returns the cached outcome; only a genuinely new request is
denied. The hot path pays nothing for this: the record lookup happens only on denial.

## 2. Edge cases and how they are handled

| Case | Handling |
|---|---|
| 30 concurrent requests, one user, funds for K | CAS serialises reservations; exactly K succeed, the rest raise the policy's access error against the post-K state |
| Concurrent requests in different processes | Redis is the serialisation point; no in-process state |
| Redelivery while the first call runs | `RESERVE` returns `DUP running`; the waiter polls and returns the same `GenerationResult`; provider called once |
| Redelivery after success | cached result, no provider call, no balance change |
| Redelivery after provider failure | cached `failed`, re-raises `GenerationFailedError`; balance already refunded |
| Provider raises | refund CAS restores the exact plan; record marked failed; other dialogs unaffected |
| Provider call cancelled | refund, then re-raise the cancellation |
| Same id, different payload | `InvalidInputError` |
| Unknown model | `InvalidInputError` before `authorize` (which would `KeyError`) |
| Balance in PG, absent in Redis | cold load under lock, exactly one SELECT, seeded only if absent |
| User with no PG row | zeros seeded, version 0; access error as the policy dictates; row created by the first flush after a mutation |
| Top-up while generations run | top-up is a CAS on the same version; either side retries; final balance = initial + Σ top-ups − Σ billed |
| Top-up redelivered | marker `topup:{operation_id}` written in the same script; second delivery is a no-op |
| Negative or over-precise top-up | `InvalidInputError`, Redis untouched |
| Two flushers flush the same user | version-guarded upsert is idempotent; rows sorted by `user_id` |
| Flusher crashes mid-batch | dirty flags survive; next tick re-flushes |
| Mutation lands during a flush | clear is skipped (version moved); user stays dirty |
| Process dies between `RESERVE` and `SETTLE`/`REFUND` | record stays `running`; the flusher's reaper refunds it after `REAP_AFTER_SECONDS` and marks it `failed` |
| Holder finishes after the reaper refunded it | `SETTLE` returns `STALE`; content discarded; `GenerationFailedError`; no double charge, no double refund |
| Provider bills less than authorized | surplus refunded LIFO along the charge order inside `SETTLE`; `billed_cost_usd` = charged |
| Provider bills more than authorized | capped at the authorized total; warning logged; raw value kept on the record |
| Transient Redis error on settle/refund | retried up to 3 times; scripts idempotent |
| Redelivery after success when the balance no longer covers the cost | cached result returned, no denial |
| CAS retries exhausted | `BalanceContentionError` (a `ConflictError`) |

## 3. Assumptions

- Single machine, several processes, one Redis and one PostgreSQL; no multi-region.
- Redis runs with `maxmemory-policy noeviction` and `appendonly yes` (compose sets AOF).
- Money never needs more than 6 decimal places (`Numeric(18, 6)`); model costs have 2.
- `free_usd` cannot be topped up through `BalanceTopUp` (the contract has no such field); tests
  seed it through the PostgreSQL row or the store.
- The delivery layer bounds redelivery age below the record TTL (24 h); a redelivery older than
  that is treated as a new operation.
- `FakeGenerationProvider` bills exactly the authorized cost.
- `client_request_id` is globally unique across users (namespaced by the caller if needed).

## 4. Known limitations (documented in README)

- Durability window: mutations after the last flush are lost if Redis loses its data before the
  next flush (`FLUSH_INTERVAL_SECONDS` + AOF fsync interval). PostgreSQL then holds an older but
  internally consistent balance and Redis reseeds from it.
- Idempotency window = record TTL. A durable PG dedup check on every cache miss was rejected
  because every new generation is a miss, which would cost one PG read per generation.
- A process that dies between `RESERVE` and `SETTLE`/`REFUND` leaves the debit standing for up to
  `REAP_AFTER_SECONDS`; a generation that legitimately runs longer than that is cancelled by the
  reaper.
- Redis retries are bounded (3 attempts); a longer outage leaves the record `running` for the
  reaper.
- Balance hashes have no TTL (≈120 B per user); idle expiry (`PERSIST` on mutation, `EXPIRE` on
  clean) is a documented extension.

## 5. Unresolved / future work

- **O2. Audit ledger in PostgreSQL** (`generation`, `top_up` tables) via the same flusher; gives
  durable history and a durable idempotency check for top-ups (rare enough to afford one read).
- **O4. Epoch-unique versions** to make a Redis data loss + reseed strictly monotonic instead of
  "self-healing on the next mutation".
- **O5. Observability**: counters for CAS conflicts, cold loads, flush batch sizes;
  `pg_stat_statements` in production to confirm the PG budget.

## 6. Implementation deviations

- **`DebitPolicyService` is provided by the DI container** (APP singleton in `entry/ioc.py`) instead
  of a dataclass `default_factory` on `GenerationService`: Dishka resolves every dataclass field as
  a dependency and refused to build the graph with the default.
- **`just check` runs the layer guard with `< /dev/null`.** `guard_layers.py` reads stdin whenever
  stdin is not a TTY (hook mode) and hung the recipe under non-interactive shells. Follow-up for
  `.claude/hooks/guard_layers.py`: fall back to scan mode when stdin is empty, otherwise the scan
  never runs outside an interactive terminal (CI included).
- **Mixed-failure test uses `paid_usd = 2.00`** (10 reservations fit) instead of the plan's
  `1.00`: with `1.00` the split between `GenerationFailedError` and `InsufficientBalanceError`
  depends on interleaving. With every reservation funded the outcome is exact: 6 ok, 4 failed,
  `paid_usd == 0.80`.
- **Multi-process workers synchronise on a `manager.Barrier`** before firing their requests so the
  three processes really contend; without it pool start-up skew let one process finish first.
- **`test_generation_cold_cache_loads_pg_balance` asserts 2 statements**, because reading the
  PostgreSQL row back for the assertion is itself a `SELECT`; the cold load is the first one.
- `GenerationService._reserve` was split out of `execute_generation` (ruff C901).
- **`BillingConfig` is split.** The adapter's knobs (`GENERATION_RECORD_TTL_SECONDS`,
  `TOP_UP_RECORD_TTL_SECONDS`, `LOAD_LOCK_TTL_MS`) moved to `RedisBalanceStoreConfig` in
  `infra/database/redis/adapters/balance_store.py`; `infra/` may not import `app/billing/`, and the
  layer guard (fixed to scan when stdin is empty) flagged the original placement. Environment keys
  are unchanged; `main/cli.py` loads both structs.
- **Code-review fixes.** `REFUND` gained the same `EXISTS` guard as `RESERVE`/`TOP_UP`
  (`RefundOutcome` now includes `Missing`; the refund loop reloads the snapshot and retries), so a
  vanished balance hash can never surface `nil` inside a Lua reply. `LOAD_WAIT_TIMEOUT_SECONDS`
  defaults to 15 s instead of 5 s so a losing cold-load caller outlives a crashed lock holder's
  `LOAD_LOCK_TTL_MS` (5 s) and gets a second attempt.
- **Reaper, reconciliation, retries (second commit).** `gen:inflight` ZSET + `ReservationReaper`
  run from the flusher tick (D16); `SETTLE` is CAS-capable and refunds the surplus LIFO, with the
  authorization as a ceiling (D7); `settle`/`refund`/`forget_inflight` retry transient Redis errors
  (D17); the refund loop moved into `ReservationRefunder` shared by the service and the reaper;
  `GenerationService` raises `GenerationFailedError` when `SETTLE` reports `STALE` instead of
  returning a refunded result. Found and fixed while testing: a redelivery of a finished
  generation was denied by the policy when the balance had since dropped below the cost (D18).
- **Charged amount clamped to `[0, authorized]`.** A provider bill below zero is treated like a
  bill above the ceiling: logged and clamped, so `surplus_refund` can never hand back more than
  the plan took.

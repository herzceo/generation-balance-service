---
paths:
  - "backend/infra/database/**/*.py"
  - "backend/app/shared/db/**/*.py"
---

# Database Rules

PostgreSQL with SQLAlchemy 2.0 async ORM (psycopg). Transaction management via the `Database`
Protocol. PostgreSQL is the durable store; the hot path never touches it (see
`rules/ports-adapters.md` for the Redis side).

## Database Protocol (app/shared/db/database.py)

```python
class Database(Protocol):
    @property
    def gateway(self) -> RepoGateway: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def flush(self) -> None: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(...) -> None: ...
```

## Transaction Management

Always wrap DB operations in `async with self.db:`:

```python
async def flush_once(self) -> int:
    ...
    async with self.db:
        await self.db.gateway.balance.upsert_many(rows)
        await self.db.commit()
    return len(rows)
```

- `async with self.db:` opens a session/transaction
- `await self.db.commit()` commits explicitly -- no auto-commit; commit after reads too, so the
  connection is released promptly
- Session auto-closes on `__aexit__`
- Supports nested transactions via `begin_nested()`
- Skip the block entirely when there is nothing to write (an empty flush costs zero statements)

## Gateway Access

Access repositories only through the gateway, only inside `async with` blocks:

```python
# correct
async with self.db:
    row = (await self.db.gateway.balance.get_by_user_id(user_id)).value
    await self.db.commit()

# wrong -- accessing gateway outside context manager
row = await self.db.gateway.balance.get_by_user_id(user_id)
```

## Session Configuration

- `expire_on_commit=False` -- entities remain usable after commit
- `autoflush=False` -- explicit flush/commit only
- Production engine from `create_async_engine(DatabaseConfig)`; tests build their own engine with
  `NullPool` (no connection reuse between tests)

## Write pattern: version-guarded upsert

The `balance` table is written only by the flusher, one multi-row statement per batch:

```python
rows = [b.to_builtins() for b in sorted(balances, key=lambda b: b.user_id)]  # sorted: no deadlocks
stmt = insert(Balance).values(rows)
stmt = stmt.on_conflict_do_update(
    index_elements=[Balance.user_id],
    set_={..., "version": stmt.excluded.version, "updated_at": func.now()},
    where=stmt.excluded.version > Balance.version,   # strict >: stale snapshots are rejected
)
await self._session.execute(stmt)
```

Custom reads use SQLAlchemy `select()` and return `Option`:

```python
async def get_by_user_id(self, user_id: UUID) -> Option[Balance]:
    result = await self._session.execute(select(Balance).where(Balance.user_id == user_id))
    return Option(result.scalar_one_or_none())
```

## Counting statements

Tests attach `event.listen(engine.sync_engine, "before_cursor_execute", ...)`. With psycopg async
this sees exactly the application's statements (no dialect init, no `BEGIN`/`COMMIT`, no pings),
so budgets are asserted as exact numbers.

## Alembic Migrations

- Migrations in `backend/infra/database/psql/alembic/migrations/versions/`
- Generate: `just migration "add x"` (needs a reachable PostgreSQL configured in `.env`); otherwise
  hand-write the revision using `script.py.mako` and the `op.f(...)` naming convention
- Apply: `just migrate`
- `env.py` imports `backend.domain.entities` to register metadata
- `tests/integration/test_migrations.py` runs `alembic.autogenerate.compare_metadata` against the
  migrated testcontainer database and asserts an empty diff; a hand-written migration that drifts
  from the entity fails there
- Never manually edit an applied migration; add a new revision instead

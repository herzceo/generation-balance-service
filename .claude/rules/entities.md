---
paths:
  - "backend/domain/entities/**/*.py"
---

# Entity Rules

Entities are SQLAlchemy ORM models in `backend/domain/entities/`. `Base`
(`entities/base/base.py`) provides the naming convention for constraints and indexes, the
automatic table name (PascalCase class name → snake_case) and `to_builtins()` used by bulk
inserts.

## Shape

```python
class Balance(Base):
    user_id: Mapped[UUID] = mapped_column(SQL_UUID(as_uuid=True), primary_key=True)
    free_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, server_default=text("0"))
    bonus_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, server_default=text("0"))
    paid_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, server_default=text("0"))
    free_requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- A natural primary key is fine when the row is addressed only by it (`user_id` here). Add a
  surrogate `id` only when rows need an identity of their own.
- Money is `Numeric(18, 6)` mapped to `Decimal`. Never `Float`.
- Prefer `server_default` over Python-side `default` so hand-written migrations and the entity
  agree and existing rows get the value.
- No mixins are needed for upsert-only tables. If a table needs `created_at`/`updated_at`
  maintained by the ORM, declare the columns explicitly on the entity.

## Column Definitions

Always use `Mapped[T]` with `mapped_column()`. Never use raw `Column()`. Always
`DateTime(timezone=True)` for timestamps.

## Relationships

Use `lazy="raise"` to prevent N+1 queries. Guard relationship type imports with `TYPE_CHECKING`
and `from __future__ import annotations`.

## Table Naming

Tables are auto-named by converting the class name from PascalCase to snake_case. Do NOT set
`__tablename__` explicitly.

## Registration

Export every new entity from `backend/domain/entities/__init__.py`. Alembic's `env.py` imports
that module to discover all models, and `tests/integration/test_migrations.py` compares the
metadata with the migrated database.

## Migration

Write the migration by hand (`/add-migration`) with the same types, nullability and server
defaults; name constraints through `op.f(...)` so they match the metadata naming convention.

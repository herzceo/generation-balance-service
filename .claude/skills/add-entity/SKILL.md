---
name: add-entity
description: Add a new domain entity with a hand-written Alembic migration and the schema drift guard. Use when defining new database tables.
argument-hint: <entity-name> <fields-comma-separated>
---

# Add a Domain Entity

Create a new SQLAlchemy ORM model and its migration.

## Arguments

- `$0` -- Entity name in PascalCase (e.g., `Balance`, `TopUpLedger`)
- `$1` -- Comma-separated fields (e.g., `user_id:uuid,paid_usd:decimal,version:int`)

## Current Entities

!`find backend/domain/entities -name "*.py" -not -name "__init__.py" -not -path "*/base/*" -not -path "*__pycache__*" 2>/dev/null | sort`

## Implementation Steps

### 1. Create entity file

File: `backend/domain/entities/{name}.py`

```python
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UUID as SQL_UUID

from .base import Base


class {Name}(Base):
    user_id: Mapped[UUID] = mapped_column(SQL_UUID(as_uuid=True), primary_key=True)
    paid_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, server_default=text("0"))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

### Column type reference

| Python Type | SQLAlchemy | Notes |
|-------------|-----------|-------|
| `str` | `String` or `String(N)` | Use `String(N)` for bounded length |
| `int` | `Integer` / `BigInteger` | `BigInteger` for counters that only grow |
| `Decimal` | `Numeric(18, 6)` | Money. Never `Float` |
| `bool` | `Boolean` | |
| `datetime` | `DateTime(timezone=True)` | Always timezone=True |
| `UUID` | `SQL_UUID(as_uuid=True)` | Primary and foreign keys |
| `str \| None` | `String, nullable=True` | `Mapped[str \| None]` |

Prefer `server_default` for every NOT NULL column so the hand-written migration and existing rows
agree with the entity.

### 2. Export from entities module

File: `backend/domain/entities/__init__.py` -- add the import and `__all__` entry so Alembic and the
drift test see the table.

### 3. Write the migration

Follow `/add-migration`: a new revision in `backend/infra/database/psql/alembic/migrations/versions/`
with `op.create_table(...)`, types and server defaults identical to the entity, constraint names
via `op.f(...)`, and a `downgrade()` that drops the table.

### 4. Verify

- `just check`
- `uv run pytest tests/integration/test_migrations.py` -- `compare_metadata` must return `[]`

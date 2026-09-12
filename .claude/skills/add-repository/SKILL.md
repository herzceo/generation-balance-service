---
name: add-repository
description: Add a new repository protocol and implementation with gateway registration. Use after creating a new entity.
argument-hint: <entity-name>
---

# Add a Repository

Create a repository Protocol (port) and its PostgreSQL implementation (adapter), then wire into the gateway.

## Arguments

- `$0` -- Entity name in PascalCase (e.g., `Balance`)

## Current Repositories

Protocol interfaces:
!`find backend/domain/repos -name "*.py" -not -name "__init__.py" -not -name "gateway.py" -not -path "*__pycache__*" 2>/dev/null | sort`

Implementations:
!`find backend/infra/database/psql/repos -name "*.py" -not -name "__init__.py" -not -name "gateway.py" -not -path "*__pycache__*" 2>/dev/null | sort`

## Implementation Steps

### 1. Create Protocol

File: `backend/domain/repos/{name}.py`

```python
from abc import abstractmethod
from typing import Protocol
from uuid import UUID

from backend.domain.entities.{name} import {Name}
from backend.internal import Option


class {Name}Repo(Protocol):
    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> Option[{Name}]: ...

    @abstractmethod
    async def upsert_many(self, rows: list[{Name}]) -> None: ...
```

Declare only the queries the application needs; lookups return `Option[T]`.

### 2. Add to Protocol gateway

File: `backend/domain/repos/gateway.py`

```python
class RepoGateway(Protocol):
    @property
    @abstractmethod
    def {name}(self) -> {Name}Repo: ...
```

### 3. Create Implementation

File: `backend/infra/database/psql/repos/{name}.py`

```python
from typing import final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.entities.{name} import {Name}
from backend.domain.repos.{name} import {Name}Repo
from backend.internal import Option


@final
class Impl{Name}Repo({Name}Repo):
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: UUID) -> Option[{Name}]:
        result = await self._session.execute(select({Name}).where({Name}.user_id == user_id))
        return Option(result.scalar_one_or_none())

    async def upsert_many(self, rows: list[{Name}]) -> None:
        if not rows:
            return
        values = [r.to_builtins() for r in sorted(rows, key=lambda r: r.user_id)]
        stmt = insert({Name}).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[{Name}.user_id],
            set_={"version": stmt.excluded.version, "updated_at": func.now()},
            where=stmt.excluded.version > {Name}.version,
        )
        await self._session.execute(stmt)
```

### 4. Add to Implementation gateway

File: `backend/infra/database/psql/repos/gateway.py`

```python
@final
class ImplRepoGateway(RepoGateway):
    @cached_property
    def {name}(self) -> Impl{Name}Repo:
        return Impl{Name}Repo(self._session)
```

### 5. Export from modules

Add to `backend/domain/repos/__init__.py` and `backend/infra/database/psql/repos/__init__.py`.

### 6. Verify

Run `just check`.

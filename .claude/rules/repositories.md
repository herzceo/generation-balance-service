---
paths:
  - "backend/domain/repos/**/*.py"
  - "backend/infra/database/psql/repos/**/*.py"
---

# Repository Rules

Repositories follow the ports & adapters pattern: Protocol in `domain/repos/`, implementation in
`infra/database/psql/repos/`. There is no generic CRUD base; each repository declares exactly the
queries the application needs.

## Protocol Definition (domain/repos/)

```python
from abc import abstractmethod
from typing import Protocol
from uuid import UUID

from backend.domain.entities.balance import Balance
from backend.internal import Option


class BalanceRepo(Protocol):
    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> Option[Balance]: ...

    @abstractmethod
    async def upsert_many(self, balances: list[Balance]) -> None: ...
```

- All lookup methods MUST return `Option[T]`, not `T | None`
- Methods are `@abstractmethod`
- Arguments and return values are entities (`domain/`), never app-layer value types

## Implementation (infra/database/psql/repos/)

```python
from typing import final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.entities.balance import Balance
from backend.domain.repos.balance import BalanceRepo
from backend.internal import Option


@final
class ImplBalanceRepo(BalanceRepo):
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: UUID) -> Option[Balance]:
        result = await self._session.execute(select(Balance).where(Balance.user_id == user_id))
        return Option(result.scalar_one_or_none())
```

- Always `@final` on implementation classes
- Inherit from the Protocol: `ImplXxxRepo(XxxRepo)`
- `__slots__ = ("_session",)`
- Bulk writes are one statement (`insert(...).values(rows)` + `on_conflict_do_update`), rows
  sorted by primary key; return early on an empty list

## Gateway Registration

Both the Protocol gateway and implementation gateway must be updated:

```python
# domain/repos/gateway.py -- add property to Protocol
class RepoGateway(Protocol):
    @property
    @abstractmethod
    def balance(self) -> BalanceRepo: ...

# infra/database/psql/repos/gateway.py -- add @cached_property to impl
@final
class ImplRepoGateway(RepoGateway):
    @cached_property
    def balance(self) -> ImplBalanceRepo:
        return ImplBalanceRepo(self._session)
```

Export new protocols from `backend/domain/repos/__init__.py` and implementations from
`backend/infra/database/psql/repos/__init__.py`.

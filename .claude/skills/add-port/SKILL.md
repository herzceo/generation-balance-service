---
name: add-port
description: Add a new port (Protocol interface) and its adapter implementation with DI wiring. Use when adding a storage or external capability.
argument-hint: <category> <port-name>
---

# Add a Port & Adapter

Create a Protocol interface (port) and its concrete implementation (adapter).

## Arguments

- `$0` -- Port category (today: `billing`)
- `$1` -- Port name (e.g., `audit_ledger`, `metrics_sink`)

## Current Ports

!`find backend/app/shared/ports -name "*.py" -not -name "__init__.py" -not -path "*__pycache__*" 2>/dev/null | sort`

## Implementation Steps

### 1. Create Port Protocol

File: `backend/app/shared/ports/{category}/{name}.py`

```python
from abc import abstractmethod
from typing import Protocol


class {PortName}(Protocol):
    @abstractmethod
    async def record(self, *, user_id: UUID, amount: Decimal) -> None: ...
```

Value types that cross the port live in the same file (or a sibling in the same package) so
`infra/` can import them from `backend.app.shared`.

### 2. Create Adapter

File: `backend/infra/database/redis/adapters/{name}.py` (Redis) or
`backend/infra/database/psql/{name}.py` (PostgreSQL)

```python
from typing import final

from redis.asyncio import Redis

from backend.app.shared.ports.{category}.{name} import {PortName}


@final
class ImplRedis{PortName}({PortName}):
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._record = redis.register_script(RECORD)

    async def record(self, *, user_id: UUID, amount: Decimal) -> None:
        await self._record(keys=[f"audit:{user_id}"], args=[format(amount, "f")])
```

Lua scripts go in `backend/infra/database/redis/scripts.py` and follow the conventions in
`rules/ports-adapters.md` (KEYS/ARGV, tag-first return arrays, money as strings, version CAS only).

### 3. Wire in DI container

File: `backend/entry/ioc.py` -- add to `create_redis_provider()` (or the psql provider):

```python
provider.provide(ImplRedis{PortName}, provides={PortName})
```

### 4. Use in services

```python
@dataclass
class SomeService:
    {port_name}: {PortName}  # inject the port, not the adapter
```

### 5. Verify

Run `just check`; integration-test the adapter through the service that uses it.

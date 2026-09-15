---
name: add-service
description: Add an application service in backend/app/billing/ for business logic that orchestrates ports. Use when adding a use case or logic shared across services.
argument-hint: <domain> <service-name>
---

# Add a Service

Create an application-layer service.

## Arguments

- `$0` -- Domain name (today: `billing`)
- `$1` -- Service name (e.g., `refund`, `report`)

## Current Services

!`find backend/app/billing -name "*.py" -not -name "__init__.py" -not -name "config.py" -not -path "*__pycache__*" 2>/dev/null | sort`

## When to Use a Service

```
Is this a use case called from outside (tests, a process entry point)?
├── Yes -> Service with a public async method
└── No
    Is this logic shared by several services (e.g. cold load)?
    ├── Yes -> Service (like BalanceLoader)
    └── No -> Keep it as a private method of the existing service
```

## Implementation Steps

### 1. Create service

File: `backend/app/billing/{name}.py`

```python
from dataclasses import dataclass

from backend.app.billing.balance_loader import BalanceLoader
from backend.app.billing.config import BillingConfig
from backend.app.shared.ports.billing.balance_store import BalanceStore


@dataclass
class {Name}Service:
    store: BalanceStore
    loader: BalanceLoader
    config: BillingConfig

    async def do_something(self, user_id: UUID) -> None:
        snapshot = await self.loader.ensure_loaded(user_id)
        ...
```

### 2. Wire in DI

File: `backend/entry/ioc.py` -- add to `create_billing_provider()`:

```python
provider.provide({Name}Service, provides={Name}Service, scope=Scope.REQUEST)
```

### 3. Export

Add to `backend/app/billing/__init__.py`.

### 4. Verify

Run `just check`, then add the integration test (`/add-test billing <scenario>`).

## Key Rules

- Services are `@dataclass` classes with DI-injected dependencies
- Services depend on ports (Protocols) and other services, never on `infra/` implementations
- All money arithmetic happens here or on `BalanceSnapshot`, in `Decimal`
- Balance mutations go through the store's CAS methods in a bounded retry loop; exhaustion raises
  `BalanceContentionError`
- Read `README.md` (`execute_generation`, `apply_top_up`) for the reference algorithms before adding a new mutation path

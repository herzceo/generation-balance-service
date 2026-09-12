---
paths:
  - "backend/**/*.py"
---

# Architecture Rules

This project follows hexagonal (ports & adapters) architecture with strict layer boundaries.

## Layers and Responsibilities

| Layer | Path | Responsibility |
|-------|------|---------------|
| `domain/` | `backend/domain/` | The given module (`generation.py`: policy, contracts, access errors, fake provider), entities (ORM models), repository Protocol interfaces |
| `app/` | `backend/app/` | Use cases (`app/billing/` services), shared ports (Protocols) and value types, errors |
| `entry/` | `backend/entry/` | Composition root: DI wiring (`ioc.py`), the flusher process bootstrap |
| `infra/` | `backend/infra/` | Concrete implementations: PostgreSQL repos and `ImplDatabase`, Redis adapter + Lua scripts, Alembic |
| `internal/` | `backend/internal/` | Utilities shared across all layers: `Option`, `StructDTO`, case conversion |
| `main/` | `backend/main/` | CLI bootstrap (`alembic`, `flusher` subcommands), env loading |

## Import Direction (enforced by guard_layers.py hook)

```
domain/   -> domain/, internal/
app/      -> app/, domain/, internal/
entry/    -> anything in backend.*
infra/    -> infra/, domain/, backend.app.shared.* (ports + db), internal/
internal/ -> internal/
main/     -> anything in backend.*
```

## Forbidden Patterns

```python
# app/ importing from infra/ -- VIOLATION
from backend.infra.database.redis.adapters.balance_store import ImplRedisBalanceStore  # wrong
from backend.infra.database.psql.repos.balance import ImplBalanceRepo                  # wrong

# app/ must use Protocol ports instead
from backend.app.shared.db.database import Database                        # correct
from backend.app.shared.ports.billing.balance_store import BalanceStore    # correct
```

```python
# infra/ importing non-shared app code -- VIOLATION
from backend.app.billing.generation import GenerationService   # wrong
from backend.app.billing.config import BillingConfig           # wrong: app/billing is not app.shared

# infra/ may only import from app.shared (ports, db, value types)
from backend.app.shared.ports.billing.balance_store import BalanceStore, BalanceSnapshot  # correct
from backend.app.shared.db.database import Database                                       # correct
```

If an infra adapter needs configuration, pass the values it needs through its own config struct
or through the port's method arguments; do not import `app/billing/config.py` from `infra/`.

```python
# domain/ importing from app/ -- VIOLATION
from backend.app.errors import NotFoundError  # wrong

# domain/ only imports from domain/ and internal/
from backend.domain.entities.base import Base  # correct
from backend.internal import Option            # correct
```

The given module `backend/domain/generation.py` imports only the standard library. It is never
edited; every layer may import its types (`GenerationRequest`, `DebitPlan`, `BalanceView`,
`MODELS`, the access errors, `FakeGenerationProvider`).

## Dependency Inversion

The `app/` layer defines abstract interfaces (Protocols) in `app/shared/ports/`. The `infra/`
layer implements them. They are wired together only in `entry/ioc.py`.

```
app/shared/ports/billing/balance_store.py       -> Protocol definition (+ value types)
infra/database/redis/adapters/balance_store.py  -> @final implementation over Lua scripts
entry/ioc.py                                    -> provider.provide(ImplRedisBalanceStore, provides=BalanceStore)
```

`FakeGenerationProvider` (domain) satisfies `GenerationProvider` (app port) structurally and is
handed to the container as an instance from the composition root or the test fixture.

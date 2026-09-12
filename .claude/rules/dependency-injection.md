---
paths:
  - "backend/entry/**/*.py"
---

# Dependency Injection Rules

DI uses Dishka, an async-first container. All wiring happens in `backend/entry/ioc.py`.

## Composition root

```python
def create_container(
    *,
    engine: AsyncEngine,
    redis: Redis,
    billing_config: BillingConfig,
    store_config: RedisBalanceStoreConfig | None = None,
    provider: GenerationProvider | None = None,
) -> AsyncContainer:
    providers = [
        create_infra_provider(engine=engine, redis=redis, billing_config=billing_config,
                              store_config=store_config or RedisBalanceStoreConfig()),
        create_billing_provider(),
    ]
    if provider is not None:
        providers.append(create_provider_provider(provider))
    return make_async_container(*providers)
```

Engine and Redis client are created by the caller (`entry/flusher.py` or the test fixture), which
also owns their lifetime (`engine.dispose()`, `redis.aclose()`). The container never creates or
closes them.

## Provider Structure

Group related bindings into provider factory functions:

```python
def create_database_provider() -> Provider:
    provider = Provider(scope=Scope.REQUEST)
    provider.provide(_create_impl_database, provides=Database)   # async generator: closes the session
    return provider
```

## Scopes

- **`Scope.APP`**: singletons -- configs, engine, session maker, Redis client, `BalanceStore`, `GenerationProvider`
- **`Scope.REQUEST`**: per-operation -- `Database`, `BalanceLoader`, `GenerationService`, `BalanceService`, `BalanceFlusher`

Callers open one REQUEST scope per operation:

```python
async with container() as request:
    service = await request.get(GenerationService)
    result = await service.execute_generation(req)
```

Thirty concurrent scopes are cheap: `ImplDatabase` creates the session object lazily and opens no
connection until a statement runs. The flusher process holds one REQUEST scope for its lifetime.

## Binding Patterns

```python
# Impl -> Protocol
provider.provide(ImplRedisBalanceStore, provides=BalanceStore)

# Config / instance as lambda
provider.provide(lambda: billing_config, provides=BillingConfig)
provider.provide(lambda: store_config, provides=RedisBalanceStoreConfig)
provider.provide(lambda: fake_provider, provides=GenerationProvider)

# Factory function
provider.provide(create_async_session_maker, provides=async_sessionmaker[AsyncSession])
```

## Key Rules

- Always bind to the Protocol type: `provides=BalanceStore`, not `provides=ImplRedisBalanceStore`
- Optional dependencies use conditional wiring: `if provider is not None: ...` (the flusher
  process has no generation provider)
- Services are `@dataclass`es whose fields are the injected dependencies; no `__init__`

## Config structs, not primitives

Never inject a bare primitive (`int`, `str`, `float`, etc.) through the DI container. The container
cannot distinguish two `int` dependencies, and callers can substitute wrong values silently.

Wrap configurable values in a typed `StructDTO` config class and inject that instead:

```python
class BillingConfig(StructDTO):
    RUNNING_WAIT_TIMEOUT_SECONDS: float = 30.0
    FLUSH_INTERVAL_SECONDS: float = 1.0

@dataclass
class BalanceFlusher:
    store: BalanceStore
    db: Database
    config: BillingConfig
```

Place the config struct with the code that owns it and in the layer that owns it: `BillingConfig`
in `app/billing/config.py` for the services, `RedisBalanceStoreConfig` (record TTLs, load-lock
TTL) next to `ImplRedisBalanceStore` in `infra/database/redis/adapters/balance_store.py`. An
adapter must never import `app/billing/` for its config — `infra/` may only import
`backend.app.shared.*`. Register each struct as a `Scope.APP` singleton via lambda and load it from
the environment with `load_from_env(...)` in `main/cli.py`.

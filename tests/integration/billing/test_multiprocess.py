"""Same user, several OS processes: Redis is the only serialisation point."""

from __future__ import annotations

import asyncio
import multiprocessing
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from redis.asyncio import from_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.shared.ports.billing import money_to_str
from backend.domain.generation import (
    FakeGenerationProvider,
    GenerationRequest,
    ProviderScenario,
    SharedProviderState,
)
from tests.integration.billing.helpers import (
    attach_pg_counter,
    basic_request,
    flush_once,
    read_pg_balance,
    read_redis_balance,
    run_generation,
    seed_pg_balance,
)
from tests.integration.billing.ioc import create_test_container

if TYPE_CHECKING:
    from multiprocessing.managers import SyncManager

    from dishka import AsyncContainer

    from backend.app.shared.ports.billing import BalanceStore
    from tests.integration.billing.helpers import PgCounter

WORKERS = 3
PER_WORKER = 10
RequestTuple = tuple[str, str, str, bool]
ResultTuple = tuple[str, str | None, str | None, str | None]


def _run_worker(
    shared: SharedProviderState,
    barrier: Any,
    redis_url: str,
    postgres_url: str,
    user_id: str,
    requests: list[RequestTuple],
) -> dict[str, Any]:
    return asyncio.run(_worker(shared, barrier, redis_url, postgres_url, user_id, requests))


async def _worker(
    shared: SharedProviderState,
    barrier: Any,
    redis_url: str,
    postgres_url: str,
    user_id: str,
    requests: list[RequestTuple],
) -> dict[str, Any]:
    engine = create_async_engine(postgres_url, poolclass=NullPool)
    counter = attach_pg_counter(engine)
    redis = from_url(redis_url, decode_responses=True)
    container = create_test_container(
        engine=engine, redis=redis, provider=FakeGenerationProvider(shared)
    )
    parsed = [
        GenerationRequest(
            user_id=UUID(user_id),
            dialog_id=UUID(dialog_id),
            client_request_id=UUID(client_request_id),
            model_name=model_name,
            conditional_free_eligible=eligible,
        )
        for client_request_id, dialog_id, model_name, eligible in requests
    ]
    try:
        barrier.wait(timeout=60)
        results = await asyncio.gather(
            *(run_generation(container, r) for r in parsed), return_exceptions=True
        )
    finally:
        await container.close()
        await redis.aclose()
        await engine.dispose()
    out: list[ResultTuple] = []
    for request, result in zip(parsed, results, strict=True):
        if isinstance(result, BaseException):
            out.append((str(request.client_request_id), None, type(result).__name__, None))
        else:
            out.append(
                (
                    str(request.client_request_id),
                    result.content,
                    None,
                    money_to_str(result.billed_cost_usd),
                )
            )
    return {"results": out, "pg_statements": counter.count}


def _as_tuple(request: GenerationRequest) -> RequestTuple:
    return (
        str(request.client_request_id),
        str(request.dialog_id),
        request.model_name,
        request.conditional_free_eligible,
    )


def _run_pool(workers: int, args: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(workers) as pool:
        return pool.starmap(_run_worker, args)


async def test_multiprocess_same_user_parallel_generations(
    container: AsyncContainer,
    store: BalanceStore,
    provider: FakeGenerationProvider,
    shared_state: SharedProviderState,
    manager: SyncManager,
    redis_url: str,
    postgres_url: str,
    pg_counter: PgCounter,
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    pg_counter.reset()
    batches = [
        [basic_request(user_id, model_name="premium") for _ in range(PER_WORKER)]
        for _ in range(WORKERS)
    ]
    for batch in batches:
        for request in batch:
            provider.set_scenario(request.client_request_id, ProviderScenario(delay_seconds=0.3))
    barrier = manager.Barrier(WORKERS)

    reports = await asyncio.to_thread(
        _run_pool,
        WORKERS,
        [
            (
                shared_state,
                barrier,
                redis_url,
                postgres_url,
                str(user_id),
                [_as_tuple(r) for r in batch],
            )
            for batch in batches
        ],
    )

    results: list[ResultTuple] = [r for report in reports for r in report["results"]]
    ok = [r for r in results if r[2] is None]
    errors = [r for r in results if r[2] is not None]
    assert len(ok) == 5
    assert len(errors) == 25
    assert {r[2] for r in errors} == {"InsufficientBalanceError"}
    assert sum(Decimal(r[3]) for r in ok if r[3] is not None) == Decimal("1.00")

    stats = provider.get_stats()
    assert len(stats.calls) == 5
    assert all(count == 1 for count in stats.calls.values())
    print(f"[multiprocess] max_active_calls={stats.max_active_calls}")
    assert stats.max_active_calls >= 2

    child_statements = sum(report["pg_statements"] for report in reports)
    print(f"[multiprocess] PG statements across {WORKERS} workers: {child_statements}")
    assert child_statements == 1

    snapshot = await read_redis_balance(store, user_id)
    assert snapshot is not None
    assert snapshot.paid_usd == Decimal(0)
    assert pg_counter.count == 0
    assert await flush_once(container) == 1
    assert pg_counter.count == 1
    row = await read_pg_balance(container, user_id)
    assert row is not None
    assert row.paid_usd == Decimal(0)
    assert row.version == snapshot.version == 5


async def test_multiprocess_redelivery_same_id_executes_once(
    container: AsyncContainer,
    store: BalanceStore,
    provider: FakeGenerationProvider,
    shared_state: SharedProviderState,
    manager: SyncManager,
    redis_url: str,
    postgres_url: str,
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    request = basic_request(user_id)
    provider.set_scenario(request.client_request_id, ProviderScenario(delay_seconds=0.5))
    barrier = manager.Barrier(2)

    reports = await asyncio.to_thread(
        _run_pool,
        2,
        [
            (shared_state, barrier, redis_url, postgres_url, str(user_id), [_as_tuple(request)])
            for _ in range(2)
        ],
    )

    results = [report["results"][0] for report in reports]
    assert all(r[2] is None for r in results)
    assert results[0][1] == results[1][1] == "generated:basic"
    assert provider.get_stats().calls[request.client_request_id] == 1
    snapshot = await read_redis_balance(store, user_id)
    assert snapshot is not None
    assert snapshot.paid_usd == Decimal("0.92")
    assert snapshot.version == 1

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from decimal import Decimal
from time import monotonic
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import event

from backend.app.billing import BalanceFlusher, BalanceService, GenerationService, ReservationReaper
from backend.app.shared.db.database import Database
from backend.app.shared.ports.billing import BalanceSnapshot, BalanceStore
from backend.domain.entities.balance import Balance
from backend.domain.generation import (
    BalanceTopUp,
    FakeGenerationProvider,
    GenerationRequest,
    GenerationResult,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass
class PgCounter:
    statements: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.statements)

    def reset(self) -> None:
        self.statements.clear()


def attach_pg_counter(engine: AsyncEngine) -> PgCounter:
    counter = PgCounter()

    def _record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        counter.statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    return counter


def basic_request(
    user_id: UUID,
    *,
    model_name: str = "basic",
    client_request_id: UUID | None = None,
    dialog_id: UUID | None = None,
    conditional_free_eligible: bool = False,
) -> GenerationRequest:
    return GenerationRequest(
        user_id=user_id,
        dialog_id=dialog_id or uuid4(),
        client_request_id=client_request_id or uuid4(),
        model_name=model_name,
        conditional_free_eligible=conditional_free_eligible,
    )


async def run_generation(container: AsyncContainer, request: GenerationRequest) -> GenerationResult:
    async with container() as c:
        service = await c.get(GenerationService)
        return await service.execute_generation(request)


async def run_top_up(container: AsyncContainer, command: BalanceTopUp) -> None:
    async with container() as c:
        service = await c.get(BalanceService)
        await service.apply_top_up(command)


async def get_balance(container: AsyncContainer, user_id: UUID) -> BalanceSnapshot:
    async with container() as c:
        service = await c.get(BalanceService)
        return await service.get_balance(user_id)


async def flush_once(container: AsyncContainer) -> int:
    async with container() as c:
        flusher = await c.get(BalanceFlusher)
        return await flusher.flush_once()


async def reap_once(container: AsyncContainer) -> int:
    async with container() as c:
        reaper = await c.get(ReservationReaper)
        return await reaper.reap_once()


@dataclass
class ScaledProvider:
    """Provider whose bill differs from the authorized cost; delegates the call to the fake."""

    inner: FakeGenerationProvider
    factor: Decimal = Decimal(1)
    override: Decimal | None = None

    async def generate(
        self, request: GenerationRequest, *, authorized_cost_usd: Decimal
    ) -> GenerationResult:
        result = await self.inner.generate(request, authorized_cost_usd=authorized_cost_usd)
        billed = self.override if self.override is not None else authorized_cost_usd * self.factor
        return GenerationResult(
            client_request_id=result.client_request_id,
            content=result.content,
            billed_cost_usd=billed,
        )


async def seed_pg_balance(
    container: AsyncContainer,
    user_id: UUID,
    *,
    free_usd: Decimal = Decimal(0),
    bonus_usd: Decimal = Decimal(0),
    paid_usd: Decimal = Decimal(0),
    free_requests: int = 0,
    version: int = 0,
) -> None:
    async with container() as c:
        db = await c.get(Database)
        async with db:
            await db.gateway.balance.upsert_many(
                [
                    Balance(
                        user_id=user_id,
                        free_usd=free_usd,
                        bonus_usd=bonus_usd,
                        paid_usd=paid_usd,
                        free_requests=free_requests,
                        version=version,
                    )
                ]
            )
            await db.commit()


async def read_pg_balance(container: AsyncContainer, user_id: UUID) -> Balance | None:
    async with container() as c:
        db = await c.get(Database)
        async with db:
            row: Balance | None = (await db.gateway.balance.get_by_user_id(user_id)).value
            await db.commit()
    return row


async def read_redis_balance(store: BalanceStore, user_id: UUID) -> BalanceSnapshot | None:
    return (await store.load(user_id)).value


async def dirty_count(redis: Redis) -> int:
    return cast("int", await redis.scard("bal:dirty"))  # type: ignore[misc]


def assert_non_negative(snapshot: BalanceSnapshot) -> None:
    assert snapshot.free_usd >= 0
    assert snapshot.bonus_usd >= 0
    assert snapshot.paid_usd >= 0
    assert snapshot.free_requests >= 0


def total_usd(snapshot: BalanceSnapshot) -> Decimal:
    return snapshot.free_usd + snapshot.bonus_usd + snapshot.paid_usd


async def wait_until(
    predicate: Callable[[], bool | Awaitable[bool]],
    *,
    max_wait: float = 5.0,
    interval: float = 0.02,
) -> None:
    deadline = monotonic() + max_wait
    while True:
        outcome = predicate()
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if outcome:
            return
        if monotonic() > deadline:
            msg = "condition not met in time"
            raise TimeoutError(msg)
        await asyncio.sleep(interval)


def split_results[T](results: list[T | BaseException]) -> tuple[list[T], list[BaseException]]:
    ok = [r for r in results if not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, BaseException)]
    return ok, errors


@dataclass
class FlakyScript:
    """Counts how many times a patched Lua script was invoked."""

    calls: int = 0


def drop_reply_once(store: BalanceStore, script: str, *, after_landing: bool) -> FlakyScript:
    """Make the first call to one Lua script look like a dropped connection.

    ``after_landing`` decides whether the script actually ran before the reply was lost, which is
    the case the retry has to recognise instead of applying the operation twice.
    """
    impl = cast("Any", store)
    original = cast("Callable[..., Awaitable[Any]]", getattr(impl, script))
    flaky = FlakyScript()

    async def patched(**kwargs: Any) -> Any:
        flaky.calls += 1
        first = flaky.calls == 1
        if first and not after_landing:
            msg = "connection dropped before the script ran"
            raise RedisConnectionError(msg)
        reply = await original(**kwargs)
        if first:
            msg = "connection dropped after the script ran"
            raise RedisConnectionError(msg)
        return reply

    setattr(impl, script, patched)
    return flaky

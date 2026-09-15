from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal
from typing import Any, cast, final
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from backend.app.shared.ports.billing.balance_store import (
    AlreadyApplied,
    Applied,
    BalanceSnapshot,
    BalanceStore,
    Conflict,
    Duplicate,
    GenerationRecord,
    GenerationStatus,
    Missing,
    RefundOutcome,
    ReserveOutcome,
    Reserved,
    SettleOutcome,
    Stale,
    TopUpOutcome,
    micros_to_money,
    money_to_micros,
    money_to_str,
    str_to_money,
)
from backend.domain.generation import BalanceTopUp, DebitPlan, GenerationRequest
from backend.infra.database.redis import scripts
from backend.internal import Option
from backend.internal.dto import StructDTO

DIRTY_KEY = "bal:dirty"
INFLIGHT_KEY = "gen:inflight"

_RETRIED_ERRORS = (RedisConnectionError, RedisTimeoutError)


class RedisBalanceStoreConfig(StructDTO):
    GENERATION_RECORD_TTL_SECONDS: int = 86400
    TOP_UP_RECORD_TTL_SECONDS: int = 86400
    LOAD_LOCK_TTL_MS: int = 5000
    RETRY_ATTEMPTS: int = 3


def balance_key(user_id: UUID) -> str:
    return f"bal:{user_id}"


def load_lock_key(user_id: UUID) -> str:
    return f"bal:load:{user_id}"


def generation_key(client_request_id: UUID) -> str:
    return f"gen:{client_request_id}"


def top_up_key(operation_id: UUID) -> str:
    return f"topup:{operation_id}"


def _micros(amount: Decimal) -> str:
    return str(money_to_micros(amount))


def _snapshot_args(snapshot: BalanceSnapshot) -> list[str]:
    return [
        _micros(snapshot.free_usd),
        _micros(snapshot.bonus_usd),
        _micros(snapshot.paid_usd),
        str(snapshot.free_requests),
    ]


def _plan_args(plan: DebitPlan) -> list[str]:
    return [
        _micros(plan.free_usd),
        _micros(plan.bonus_usd),
        _micros(plan.paid_usd),
        str(plan.free_requests),
    ]


def _snapshot_from_conflict(reply: list[str]) -> BalanceSnapshot:
    return BalanceSnapshot(
        free_usd=micros_to_money(int(reply[1])),
        bonus_usd=micros_to_money(int(reply[2])),
        paid_usd=micros_to_money(int(reply[3])),
        free_requests=int(reply[4]),
        version=int(reply[5]),
    )


def _snapshot_from_hash(fields: dict[str, str]) -> BalanceSnapshot:
    return BalanceSnapshot(
        free_usd=micros_to_money(int(fields["free_usd"])),
        bonus_usd=micros_to_money(int(fields["bonus_usd"])),
        paid_usd=micros_to_money(int(fields["paid_usd"])),
        free_requests=int(fields["free_requests"]),
        version=int(fields["version"]),
    )


def _record_from_fields(fields: dict[str, str]) -> GenerationRecord:
    raw_bill = fields.get("provider_billed_cost_usd")
    return GenerationRecord(
        status=GenerationStatus(fields["status"]),
        user_id=UUID(fields["user_id"]),
        dialog_id=UUID(fields["dialog_id"]),
        model_name=fields["model_name"],
        plan=DebitPlan(
            free_usd=micros_to_money(int(fields["plan_free_usd"])),
            bonus_usd=micros_to_money(int(fields["plan_bonus_usd"])),
            paid_usd=micros_to_money(int(fields["plan_paid_usd"])),
            free_requests=int(fields["plan_free_requests"]),
        ),
        authorized_cost_usd=micros_to_money(int(fields["authorized_cost_usd"])),
        started_at=float(fields["started_at"]),
        content=fields.get("content") or None,
        billed_cost_usd=_optional_micros(fields, "billed_cost_usd"),
        provider_billed_cost_usd=str_to_money(raw_bill) if raw_bill else None,
        refunded_surplus_usd=_optional_micros(fields, "refunded_surplus_usd"),
        error=fields.get("error") or None,
    )


def _optional_micros(fields: dict[str, str], name: str) -> Decimal | None:
    raw = fields.get(name)
    return micros_to_money(int(raw)) if raw else None


_DUP_FIELDS = (
    "status",
    "user_id",
    "dialog_id",
    "model_name",
    "plan_free_usd",
    "plan_bonus_usd",
    "plan_paid_usd",
    "plan_free_requests",
    "authorized_cost_usd",
    "started_at",
    "content",
    "billed_cost_usd",
    "provider_billed_cost_usd",
    "refunded_surplus_usd",
    "error",
    "reservation_token",
)


@final
class ImplRedisBalanceStore(BalanceStore):
    __slots__ = (
        "_advance_version",
        "_clear_dirty",
        "_config",
        "_redis",
        "_refund",
        "_release_load_lock",
        "_reserve",
        "_seed",
        "_settle",
        "_top_up",
    )

    def __init__(self, redis: Redis, config: RedisBalanceStoreConfig) -> None:
        self._redis = redis
        self._config = config
        self._advance_version = redis.register_script(scripts.ADVANCE_VERSION)
        self._reserve = redis.register_script(scripts.RESERVE)
        self._settle = redis.register_script(scripts.SETTLE)
        self._refund = redis.register_script(scripts.REFUND)
        self._top_up = redis.register_script(scripts.TOP_UP)
        self._seed = redis.register_script(scripts.SEED_IF_ABSENT)
        self._release_load_lock = redis.register_script(scripts.RELEASE_LOAD_LOCK)
        self._clear_dirty = redis.register_script(scripts.CLEAR_DIRTY)

    async def _retrying[T](self, call: Callable[[], Awaitable[T]]) -> T:
        """Retry on transport errors; every wrapped script recognises its own landed call."""
        attempt = 1
        while True:
            try:
                return await call()
            except _RETRIED_ERRORS:
                if attempt >= self._config.RETRY_ATTEMPTS:
                    raise
                await asyncio.sleep(0.05 * attempt)
                attempt += 1

    async def load(self, user_id: UUID) -> Option[BalanceSnapshot]:
        fields = cast("dict[str, str]", await self._redis.hgetall(balance_key(user_id)))  # type: ignore[misc]
        return Option(_snapshot_from_hash(fields) if fields else None)

    async def load_many(self, user_ids: Sequence[UUID]) -> dict[UUID, BalanceSnapshot]:
        if not user_ids:
            return {}
        async with self._redis.pipeline(transaction=False) as pipe:
            for user_id in user_ids:
                pipe.hgetall(balance_key(user_id))
            replies = cast("list[dict[str, str]]", await pipe.execute())
        return {
            user_id: _snapshot_from_hash(fields)
            for user_id, fields in zip(user_ids, replies, strict=True)
            if fields
        }

    async def seed_if_absent(self, user_id: UUID, snapshot: BalanceSnapshot) -> bool:
        reply = await self._seed(
            keys=[balance_key(user_id)],
            args=[*_snapshot_args(snapshot), str(snapshot.version)],
        )
        return bool(reply)

    async def try_acquire_load_lock(self, user_id: UUID) -> str | None:
        token = uuid4().hex
        acquired = await self._redis.set(
            load_lock_key(user_id), token, nx=True, px=self._config.LOAD_LOCK_TTL_MS
        )
        return token if acquired else None

    async def release_load_lock(self, user_id: UUID, token: str) -> None:
        await self._release_load_lock(keys=[load_lock_key(user_id)], args=[token])

    async def advance_version(self, user_id: UUID, *, expected: int, to: int) -> bool:
        reply = await self._retrying(
            lambda: self._advance_version(
                keys=[balance_key(user_id), DIRTY_KEY], args=[str(expected), str(to), str(user_id)]
            )
        )
        return bool(reply)

    async def reserve(
        self,
        *,
        request: GenerationRequest,
        expected_version: int,
        plan: DebitPlan,
        authorized_cost_usd: Decimal,
        started_at: float,
    ) -> ReserveOutcome:
        token = uuid4().hex
        reply = cast(
            "list[str]",
            await self._retrying(
                lambda: self._reserve(
                    keys=[
                        balance_key(request.user_id),
                        generation_key(request.client_request_id),
                        DIRTY_KEY,
                        INFLIGHT_KEY,
                    ],
                    args=[
                        str(expected_version),
                        str(request.user_id),
                        str(request.dialog_id),
                        request.model_name,
                        *_plan_args(plan),
                        _micros(authorized_cost_usd),
                        repr(started_at),
                        str(self._config.GENERATION_RECORD_TTL_SECONDS),
                        str(request.client_request_id),
                        token,
                    ],
                )
            ),
        )
        match reply[0]:
            case "OK":
                return Reserved()
            case "CONFLICT":
                return Conflict(current=_snapshot_from_conflict(reply))
            case "MISSING":
                return Missing()
            case _:
                fields = dict(zip(_DUP_FIELDS, reply[1:], strict=True))
                if fields["reservation_token"] == token:
                    return Reserved()
                return Duplicate(record=_record_from_fields(fields))

    async def get_generation(self, client_request_id: UUID) -> Option[GenerationRecord]:
        fields = cast(
            "dict[str, str]",
            await self._redis.hgetall(generation_key(client_request_id)),  # type: ignore[misc]
        )
        return Option(_record_from_fields(fields) if fields else None)

    async def settle(
        self,
        *,
        user_id: UUID,
        client_request_id: UUID,
        content: str,
        billed_cost_usd: Decimal,
        provider_billed_cost_usd: Decimal,
        refund: DebitPlan,
    ) -> SettleOutcome:
        reply = cast(
            "list[str]",
            await self._retrying(
                lambda: self._settle(
                    keys=[
                        generation_key(client_request_id),
                        balance_key(user_id),
                        DIRTY_KEY,
                        INFLIGHT_KEY,
                    ],
                    args=[
                        content,
                        _micros(billed_cost_usd),
                        money_to_str(provider_billed_cost_usd),
                        str(self._config.GENERATION_RECORD_TTL_SECONDS),
                        str(client_request_id),
                        str(user_id),
                        _micros(refund.free_usd),
                        _micros(refund.bonus_usd),
                        _micros(refund.paid_usd),
                        _micros(refund.total_usd),
                    ],
                )
            ),
        )
        match reply[0]:
            case "OK":
                return Applied()
            case "DONE":
                return AlreadyApplied()
            case "MISSING":
                return Missing()
            case _:
                return Stale()

    async def refund(
        self, *, user_id: UUID, client_request_id: UUID, plan: DebitPlan, error: str
    ) -> RefundOutcome:
        reply = cast(
            "list[str]",
            await self._retrying(
                lambda: self._refund(
                    keys=[
                        balance_key(user_id),
                        generation_key(client_request_id),
                        DIRTY_KEY,
                        INFLIGHT_KEY,
                    ],
                    args=[
                        str(user_id),
                        error,
                        str(self._config.GENERATION_RECORD_TTL_SECONDS),
                        str(client_request_id),
                        *_plan_args(plan),
                    ],
                )
            ),
        )
        match reply[0]:
            case "OK":
                return Applied()
            case "MISSING":
                return Missing()
            case _:
                return Stale()

    async def top_up(self, command: BalanceTopUp) -> TopUpOutcome:
        reply = cast(
            "list[str]",
            await self._retrying(
                lambda: self._top_up(
                    keys=[
                        balance_key(command.user_id),
                        top_up_key(command.operation_id),
                        DIRTY_KEY,
                    ],
                    args=[
                        str(command.user_id),
                        str(self._config.TOP_UP_RECORD_TTL_SECONDS),
                        _micros(command.paid_usd),
                        _micros(command.bonus_usd),
                        str(command.free_requests),
                    ],
                )
            ),
        )
        match reply[0]:
            case "OK":
                return Applied()
            case "MISSING":
                return Missing()
            case _:
                return AlreadyApplied()

    async def stale_inflight(self, *, older_than: float, limit: int) -> list[UUID]:
        members = cast(
            "list[str]",
            await self._redis.zrangebyscore(INFLIGHT_KEY, "-inf", older_than, start=0, num=limit),
        )
        return [UUID(member) for member in members]

    async def forget_inflight(self, client_request_id: UUID) -> None:
        await self._retrying(lambda: self._redis.zrem(INFLIGHT_KEY, str(client_request_id)))

    async def dirty_users(self, limit: int) -> list[UUID]:
        members = cast("list[str]", await self._redis.srandmember(DIRTY_KEY, limit))  # type: ignore[misc]
        return [UUID(member) for member in members]

    async def clear_dirty(self, flushed: Sequence[tuple[UUID, int]]) -> None:
        if not flushed:
            return
        async with self._redis.pipeline(transaction=False) as pipe:
            for user_id, version in flushed:
                await self._clear_dirty(
                    keys=[balance_key(user_id), DIRTY_KEY],
                    args=[str(version), str(user_id)],
                    client=cast("Any", pipe),
                )
            await pipe.execute()

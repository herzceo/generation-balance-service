from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal
from typing import Any, cast, final
from uuid import UUID

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
    money_to_str,
    str_to_money,
)
from backend.domain.generation import DebitPlan, GenerationRequest
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


def _snapshot_args(snapshot: BalanceSnapshot) -> list[str]:
    return [
        money_to_str(snapshot.free_usd),
        money_to_str(snapshot.bonus_usd),
        money_to_str(snapshot.paid_usd),
        str(snapshot.free_requests),
    ]


def _snapshot_from_conflict(reply: list[str]) -> BalanceSnapshot:
    return BalanceSnapshot(
        free_usd=str_to_money(reply[1]),
        bonus_usd=str_to_money(reply[2]),
        paid_usd=str_to_money(reply[3]),
        free_requests=int(reply[4]),
        version=int(reply[5]),
    )


def _snapshot_from_hash(fields: dict[str, str]) -> BalanceSnapshot:
    return BalanceSnapshot(
        free_usd=str_to_money(fields["free_usd"]),
        bonus_usd=str_to_money(fields["bonus_usd"]),
        paid_usd=str_to_money(fields["paid_usd"]),
        free_requests=int(fields["free_requests"]),
        version=int(fields["version"]),
    )


def _record_from_fields(fields: dict[str, str]) -> GenerationRecord:
    return GenerationRecord(
        status=GenerationStatus(fields["status"]),
        user_id=UUID(fields["user_id"]),
        dialog_id=UUID(fields["dialog_id"]),
        model_name=fields["model_name"],
        plan=DebitPlan(
            free_usd=str_to_money(fields["plan_free_usd"]),
            bonus_usd=str_to_money(fields["plan_bonus_usd"]),
            paid_usd=str_to_money(fields["plan_paid_usd"]),
            free_requests=int(fields["plan_free_requests"]),
        ),
        authorized_cost_usd=str_to_money(fields["authorized_cost_usd"]),
        started_at=float(fields["started_at"]),
        content=fields.get("content") or None,
        billed_cost_usd=_optional_money(fields, "billed_cost_usd"),
        provider_billed_cost_usd=_optional_money(fields, "provider_billed_cost_usd"),
        refunded_surplus_usd=_optional_money(fields, "refunded_surplus_usd"),
        error=fields.get("error") or None,
    )


def _optional_money(fields: dict[str, str], name: str) -> Decimal | None:
    raw = fields.get(name)
    return str_to_money(raw) if raw else None


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
)


@final
class ImplRedisBalanceStore(BalanceStore):
    __slots__ = (
        "_clear_dirty",
        "_config",
        "_redis",
        "_refund",
        "_reserve",
        "_seed",
        "_settle",
        "_top_up",
    )

    def __init__(self, redis: Redis, config: RedisBalanceStoreConfig) -> None:
        self._redis = redis
        self._config = config
        self._reserve = redis.register_script(scripts.RESERVE)
        self._settle = redis.register_script(scripts.SETTLE)
        self._refund = redis.register_script(scripts.REFUND)
        self._top_up = redis.register_script(scripts.TOP_UP)
        self._seed = redis.register_script(scripts.SEED_IF_ABSENT)
        self._clear_dirty = redis.register_script(scripts.CLEAR_DIRTY)

    async def _retrying[T](self, call: Callable[[], Awaitable[T]]) -> T:
        """Re-issue a money-path command on transient transport errors; scripts are idempotent."""
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

    async def try_acquire_load_lock(self, user_id: UUID) -> bool:
        acquired = await self._redis.set(
            load_lock_key(user_id), "1", nx=True, px=self._config.LOAD_LOCK_TTL_MS
        )
        return bool(acquired)

    async def release_load_lock(self, user_id: UUID) -> None:
        await self._redis.delete(load_lock_key(user_id))

    async def reserve(
        self,
        *,
        request: GenerationRequest,
        expected_version: int,
        new: BalanceSnapshot,
        plan: DebitPlan,
        authorized_cost_usd: Decimal,
        started_at: float,
    ) -> ReserveOutcome:
        reply = cast(
            "list[str]",
            await self._reserve(
                keys=[
                    balance_key(request.user_id),
                    generation_key(request.client_request_id),
                    DIRTY_KEY,
                    INFLIGHT_KEY,
                ],
                args=[
                    str(expected_version),
                    *_snapshot_args(new),
                    str(request.user_id),
                    str(request.dialog_id),
                    request.model_name,
                    money_to_str(plan.free_usd),
                    money_to_str(plan.bonus_usd),
                    money_to_str(plan.paid_usd),
                    str(plan.free_requests),
                    money_to_str(authorized_cost_usd),
                    repr(started_at),
                    str(self._config.GENERATION_RECORD_TTL_SECONDS),
                    str(request.client_request_id),
                ],
            ),
        )
        match reply[0]:
            case "OK":
                return Reserved(version=int(reply[1]))
            case "CONFLICT":
                return Conflict(current=_snapshot_from_conflict(reply))
            case "MISSING":
                return Missing()
            case _:
                fields = dict(zip(_DUP_FIELDS, reply[1:], strict=True))
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
        refunded_surplus_usd: Decimal,
        expected_version: int | None,
        new: BalanceSnapshot | None,
    ) -> SettleOutcome:
        balance_args = _snapshot_args(new) if new is not None else ["", "", "", ""]
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
                        money_to_str(billed_cost_usd),
                        money_to_str(provider_billed_cost_usd),
                        str(self._config.GENERATION_RECORD_TTL_SECONDS),
                        str(client_request_id),
                        str(user_id),
                        "" if expected_version is None else str(expected_version),
                        *balance_args,
                        money_to_str(refunded_surplus_usd),
                    ],
                )
            ),
        )
        match reply[0]:
            case "OK":
                return Applied()
            case "CONFLICT":
                return Conflict(current=_snapshot_from_conflict(reply))
            case "MISSING":
                return Missing()
            case _:
                return Stale()

    async def refund(
        self,
        *,
        user_id: UUID,
        client_request_id: UUID,
        expected_version: int,
        new: BalanceSnapshot,
        error: str,
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
                        str(expected_version),
                        *_snapshot_args(new),
                        str(user_id),
                        error,
                        str(self._config.GENERATION_RECORD_TTL_SECONDS),
                        str(client_request_id),
                    ],
                )
            ),
        )
        match reply[0]:
            case "OK":
                return Applied()
            case "CONFLICT":
                return Conflict(current=_snapshot_from_conflict(reply))
            case "MISSING":
                return Missing()
            case _:
                return Stale()

    async def top_up(
        self,
        *,
        operation_id: UUID,
        user_id: UUID,
        expected_version: int,
        new: BalanceSnapshot,
    ) -> TopUpOutcome:
        reply = cast(
            "list[str]",
            await self._top_up(
                keys=[balance_key(user_id), top_up_key(operation_id), DIRTY_KEY],
                args=[
                    str(expected_version),
                    *_snapshot_args(new),
                    str(user_id),
                    str(self._config.TOP_UP_RECORD_TTL_SECONDS),
                ],
            ),
        )
        match reply[0]:
            case "OK":
                return Applied()
            case "CONFLICT":
                return Conflict(current=_snapshot_from_conflict(reply))
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

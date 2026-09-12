from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast, final
from uuid import UUID

from redis.asyncio import Redis

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


class RedisBalanceStoreConfig(StructDTO):
    GENERATION_RECORD_TTL_SECONDS: int = 86400
    TOP_UP_RECORD_TTL_SECONDS: int = 86400
    LOAD_LOCK_TTL_MS: int = 5000


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
        billed_cost_usd=str_to_money(fields["billed_cost_usd"])
        if fields.get("billed_cost_usd")
        else None,
        error=fields.get("error") or None,
    )


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
        self, client_request_id: UUID, *, content: str, billed_cost_usd: Decimal
    ) -> bool:
        reply = await self._settle(
            keys=[generation_key(client_request_id)],
            args=[
                content,
                money_to_str(billed_cost_usd),
                str(self._config.GENERATION_RECORD_TTL_SECONDS),
            ],
        )
        return bool(reply)

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
            await self._refund(
                keys=[balance_key(user_id), generation_key(client_request_id), DIRTY_KEY],
                args=[
                    str(expected_version),
                    *_snapshot_args(new),
                    str(user_id),
                    error,
                    str(self._config.GENERATION_RECORD_TTL_SECONDS),
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

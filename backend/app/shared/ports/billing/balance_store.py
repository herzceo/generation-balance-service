from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from backend.domain.generation import BalanceTopUp, DebitPlan, GenerationRequest
from backend.internal import Option
from backend.internal.dto import StructDTO


def money_to_str(amount: Decimal) -> str:
    return format(amount, "f")


def str_to_money(raw: str) -> Decimal:
    return Decimal(raw)


class BalanceSnapshot(StructDTO, kw_only=True):
    """Point-in-time balance; satisfies ``BalanceView`` for ``DebitPolicyService``."""

    free_usd: Decimal
    bonus_usd: Decimal
    paid_usd: Decimal
    free_requests: int
    version: int

    @classmethod
    def zero(cls) -> BalanceSnapshot:
        return cls(
            free_usd=Decimal(0),
            bonus_usd=Decimal(0),
            paid_usd=Decimal(0),
            free_requests=0,
            version=0,
        )

    def apply_plan(self, plan: DebitPlan) -> BalanceSnapshot:
        return BalanceSnapshot(
            free_usd=self.free_usd - plan.free_usd,
            bonus_usd=self.bonus_usd - plan.bonus_usd,
            paid_usd=self.paid_usd - plan.paid_usd,
            free_requests=self.free_requests - plan.free_requests,
            version=self.version,
        )

    def refund_plan(self, plan: DebitPlan) -> BalanceSnapshot:
        return BalanceSnapshot(
            free_usd=self.free_usd + plan.free_usd,
            bonus_usd=self.bonus_usd + plan.bonus_usd,
            paid_usd=self.paid_usd + plan.paid_usd,
            free_requests=self.free_requests + plan.free_requests,
            version=self.version,
        )

    def apply_top_up(self, top_up: BalanceTopUp) -> BalanceSnapshot:
        return BalanceSnapshot(
            free_usd=self.free_usd,
            bonus_usd=self.bonus_usd + top_up.bonus_usd,
            paid_usd=self.paid_usd + top_up.paid_usd,
            free_requests=self.free_requests + top_up.free_requests,
            version=self.version,
        )


class GenerationStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class GenerationRecord(StructDTO, kw_only=True):
    status: GenerationStatus
    user_id: UUID
    dialog_id: UUID
    model_name: str
    plan: DebitPlan
    authorized_cost_usd: Decimal
    started_at: float
    content: str | None = None
    billed_cost_usd: Decimal | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Reserved:
    version: int


@dataclass(frozen=True, slots=True)
class Conflict:
    current: BalanceSnapshot


@dataclass(frozen=True, slots=True)
class Missing: ...


@dataclass(frozen=True, slots=True)
class Duplicate:
    record: GenerationRecord


@dataclass(frozen=True, slots=True)
class Applied: ...


@dataclass(frozen=True, slots=True)
class AlreadyApplied: ...


@dataclass(frozen=True, slots=True)
class Stale: ...


type ReserveOutcome = Reserved | Conflict | Missing | Duplicate
type RefundOutcome = Applied | Conflict | Missing | Stale
type TopUpOutcome = Applied | Conflict | Missing | AlreadyApplied


class BalanceStore(Protocol):
    """Hot balance store. Every mutation is a compare-and-set on the per-user ``version``."""

    @abstractmethod
    async def load(self, user_id: UUID) -> Option[BalanceSnapshot]: ...

    @abstractmethod
    async def load_many(self, user_ids: Sequence[UUID]) -> dict[UUID, BalanceSnapshot]: ...

    @abstractmethod
    async def seed_if_absent(self, user_id: UUID, snapshot: BalanceSnapshot) -> bool: ...

    @abstractmethod
    async def try_acquire_load_lock(self, user_id: UUID) -> bool: ...

    @abstractmethod
    async def release_load_lock(self, user_id: UUID) -> None: ...

    @abstractmethod
    async def reserve(
        self,
        *,
        request: GenerationRequest,
        expected_version: int,
        new: BalanceSnapshot,
        plan: DebitPlan,
        authorized_cost_usd: Decimal,
        started_at: float,
    ) -> ReserveOutcome: ...

    @abstractmethod
    async def get_generation(self, client_request_id: UUID) -> Option[GenerationRecord]: ...

    @abstractmethod
    async def settle(
        self, client_request_id: UUID, *, content: str, billed_cost_usd: Decimal
    ) -> bool: ...

    @abstractmethod
    async def refund(
        self,
        *,
        user_id: UUID,
        client_request_id: UUID,
        expected_version: int,
        new: BalanceSnapshot,
        error: str,
    ) -> RefundOutcome: ...

    @abstractmethod
    async def top_up(
        self,
        *,
        operation_id: UUID,
        user_id: UUID,
        expected_version: int,
        new: BalanceSnapshot,
    ) -> TopUpOutcome: ...

    @abstractmethod
    async def dirty_users(self, limit: int) -> list[UUID]: ...

    @abstractmethod
    async def clear_dirty(self, flushed: Sequence[tuple[UUID, int]]) -> None: ...

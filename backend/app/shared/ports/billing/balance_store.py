from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from backend.domain.generation import BalanceTopUp, DebitPlan, DebitPolicy, GenerationRequest
from backend.internal import Option
from backend.internal.dto import StructDTO


def money_to_str(amount: Decimal) -> str:
    return format(amount, "f")


def str_to_money(raw: str) -> Decimal:
    return Decimal(raw)


_CHARGE_ORDER: dict[DebitPolicy, tuple[str, ...]] = {
    DebitPolicy.PAID_ONLY: ("paid",),
    DebitPolicy.PAID_THEN_FREE_THEN_BONUS: ("paid", "free", "bonus"),
    DebitPolicy.DEFAULT: ("free", "bonus", "paid"),
}


def surplus_refund(plan: DebitPlan, policy: DebitPolicy, billed_cost_usd: Decimal) -> DebitPlan:
    """Amounts to give back when the provider billed less than the authorized plan.

    The authorization is a ceiling: billing above it is capped, never charged. The surplus is
    returned LIFO along the policy's charge order, so the bucket charged last is refunded first.
    ``free_requests`` are never refunded here.
    """
    charged = {"free": plan.free_usd, "bonus": plan.bonus_usd, "paid": plan.paid_usd}
    remaining = plan.total_usd - max(Decimal(0), min(billed_cost_usd, plan.total_usd))
    refund = {"free": Decimal(0), "bonus": Decimal(0), "paid": Decimal(0)}
    for bucket in reversed(_CHARGE_ORDER[policy]):
        taken = min(charged[bucket], remaining)
        refund[bucket] = taken
        remaining -= taken
        if remaining == 0:
            break
    return DebitPlan(free_usd=refund["free"], bonus_usd=refund["bonus"], paid_usd=refund["paid"])


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
    provider_billed_cost_usd: Decimal | None = None
    refunded_surplus_usd: Decimal | None = None
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
type SettleOutcome = Applied | Conflict | Missing | Stale
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
        """Mark the record done; with ``expected_version``/``new`` also refund a surplus by CAS."""
        ...

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
    async def stale_inflight(self, *, older_than: float, limit: int) -> list[UUID]: ...

    @abstractmethod
    async def forget_inflight(self, client_request_id: UUID) -> None: ...

    @abstractmethod
    async def dirty_users(self, limit: int) -> list[UUID]: ...

    @abstractmethod
    async def clear_dirty(self, flushed: Sequence[tuple[UUID, int]]) -> None: ...

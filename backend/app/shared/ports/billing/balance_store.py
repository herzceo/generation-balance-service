from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from backend.domain.generation import BalanceTopUp, DebitPlan, DebitPolicy, GenerationRequest
from backend.internal import Option
from backend.internal.dto import StructDTO

MONEY_QUANTUM = Decimal("0.000001")


def money_to_str(amount: Decimal) -> str:
    return format(amount, "f")


def str_to_money(raw: str) -> Decimal:
    return Decimal(raw)


def quantize_money(amount: Decimal) -> Decimal:
    """Round down to the six decimals PostgreSQL stores, so Redis and the row cannot diverge."""
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)


def money_to_micros(amount: Decimal) -> int:
    """Integer micro-dollars; anything finer than ``MONEY_QUANTUM`` is a caller bug."""
    scaled = amount.scaleb(6)
    if scaled != scaled.to_integral_value():
        msg = f"{amount} is finer than {MONEY_QUANTUM}"
        raise ValueError(msg)
    return int(scaled)


def micros_to_money(micros: int) -> Decimal:
    return Decimal(micros).scaleb(-6)


_CHARGE_ORDER: dict[DebitPolicy, tuple[str, ...]] = {
    DebitPolicy.PAID_ONLY: ("paid",),
    DebitPolicy.PAID_THEN_FREE_THEN_BONUS: ("paid", "free", "bonus"),
    DebitPolicy.DEFAULT: ("free", "bonus", "paid"),
}


def surplus_refund(plan: DebitPlan, policy: DebitPolicy, billed_cost_usd: Decimal) -> DebitPlan:
    """Unbilled part of the plan, returned LIFO along the charge order; ``free_requests`` stay."""
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
class Reserved: ...


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
type SettleOutcome = Applied | AlreadyApplied | Missing | Stale
type RefundOutcome = Applied | Missing | Stale
type TopUpOutcome = Applied | Missing | AlreadyApplied


class BalanceStore(Protocol):
    """Hot balance store: ``reserve`` is a CAS on ``version``, every other mutation is additive."""

    @abstractmethod
    async def load(self, user_id: UUID) -> Option[BalanceSnapshot]: ...

    @abstractmethod
    async def load_many(self, user_ids: Sequence[UUID]) -> dict[UUID, BalanceSnapshot]: ...

    @abstractmethod
    async def seed_if_absent(self, user_id: UUID, snapshot: BalanceSnapshot) -> bool: ...

    @abstractmethod
    async def try_acquire_load_lock(self, user_id: UUID) -> str | None:
        """Return the owner token of the acquired lock, or ``None`` when someone else holds it."""
        ...

    @abstractmethod
    async def release_load_lock(self, user_id: UUID, token: str) -> None:
        """Delete the lock only while ``token`` still holds it."""
        ...

    @abstractmethod
    async def advance_version(self, user_id: UUID, *, expected: int, to: int) -> bool:
        """CAS ``version`` from ``expected`` to ``to`` and mark the user dirty."""
        ...

    @abstractmethod
    async def reserve(
        self,
        *,
        request: GenerationRequest,
        expected_version: int,
        plan: DebitPlan,
        authorized_cost_usd: Decimal,
        started_at: float,
    ) -> ReserveOutcome:
        """Debit ``plan`` if ``version`` still equals ``expected_version``; create the record."""
        ...

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
        refund: DebitPlan,
    ) -> SettleOutcome:
        """Mark the record done and add ``refund`` (the unbilled part of the plan) back."""
        ...

    @abstractmethod
    async def refund(
        self, *, user_id: UUID, client_request_id: UUID, plan: DebitPlan, error: str
    ) -> RefundOutcome:
        """Add the reserved ``plan`` back and mark the record failed; no-op unless running."""
        ...

    @abstractmethod
    async def top_up(self, command: BalanceTopUp) -> TopUpOutcome:
        """Add the top-up once per ``operation_id``."""
        ...

    @abstractmethod
    async def stale_inflight(self, *, older_than: float, limit: int) -> list[UUID]: ...

    @abstractmethod
    async def forget_inflight(self, client_request_id: UUID) -> None: ...

    @abstractmethod
    async def dirty_users(self, limit: int) -> list[UUID]: ...

    @abstractmethod
    async def clear_dirty(self, flushed: Sequence[tuple[UUID, int]]) -> None: ...

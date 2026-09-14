from decimal import Decimal

from backend.app.shared.ports.billing import surplus_refund
from backend.domain.generation import DebitPlan, DebitPolicy


def test_zero_surplus_refunds_nothing() -> None:
    plan = DebitPlan(paid_usd=Decimal("0.20"))
    assert surplus_refund(plan, DebitPolicy.PAID_ONLY, Decimal("0.20")) == DebitPlan()


def test_partial_surplus_returns_lifo_across_buckets() -> None:
    plan = DebitPlan(free_usd=Decimal("0.05"), paid_usd=Decimal("0.03"))
    refund = surplus_refund(plan, DebitPolicy.DEFAULT, Decimal("0.03"))
    assert refund == DebitPlan(free_usd=Decimal("0.02"), paid_usd=Decimal("0.03"))


def test_paid_then_free_then_bonus_refunds_bonus_first() -> None:
    plan = DebitPlan(paid_usd=Decimal("0.02"), free_usd=Decimal("0.03"), bonus_usd=Decimal("0.02"))
    refund = surplus_refund(plan, DebitPolicy.PAID_THEN_FREE_THEN_BONUS, Decimal("0.04"))
    assert refund == DebitPlan(bonus_usd=Decimal("0.02"), free_usd=Decimal("0.01"))


def test_billed_above_authorized_refunds_nothing() -> None:
    plan = DebitPlan(paid_usd=Decimal("0.20"))
    assert surplus_refund(plan, DebitPolicy.PAID_ONLY, Decimal("0.50")) == DebitPlan()


def test_free_requests_never_refunded() -> None:
    plan = DebitPlan(free_usd=Decimal("0.05"), free_requests=1)
    refund = surplus_refund(plan, DebitPolicy.DEFAULT, Decimal(0))
    assert refund == DebitPlan(free_usd=Decimal("0.05"), free_requests=0)

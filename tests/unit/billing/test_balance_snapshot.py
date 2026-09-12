from decimal import Decimal
from uuid import uuid4

from backend.app.shared.ports.billing import BalanceSnapshot, money_to_str, str_to_money
from backend.domain.generation import BalanceTopUp, DebitPlan


def _snapshot() -> BalanceSnapshot:
    return BalanceSnapshot(
        free_usd=Decimal("0.10"),
        bonus_usd=Decimal("0.20"),
        paid_usd=Decimal("1.00"),
        free_requests=3,
        version=7,
    )


def test_apply_plan_subtracts_every_bucket() -> None:
    plan = DebitPlan(
        free_usd=Decimal("0.05"),
        bonus_usd=Decimal("0.20"),
        paid_usd=Decimal("0.08"),
        free_requests=1,
    )
    result = _snapshot().apply_plan(plan)
    assert result.free_usd == Decimal("0.05")
    assert result.bonus_usd == Decimal(0)
    assert result.paid_usd == Decimal("0.92")
    assert result.free_requests == 2
    assert result.version == 7


def test_refund_plan_restores_original() -> None:
    plan = DebitPlan(free_usd=Decimal("0.10"), paid_usd=Decimal("0.03"), free_requests=1)
    original = _snapshot()
    assert original.apply_plan(plan).refund_plan(plan) == original


def test_apply_top_up_leaves_free_usd_untouched() -> None:
    top_up = BalanceTopUp(
        operation_id=uuid4(),
        user_id=uuid4(),
        paid_usd=Decimal("2.50"),
        bonus_usd=Decimal("0.05"),
        free_requests=2,
    )
    result = _snapshot().apply_top_up(top_up)
    assert result.free_usd == Decimal("0.10")
    assert result.bonus_usd == Decimal("0.25")
    assert result.paid_usd == Decimal("3.50")
    assert result.free_requests == 5


def test_zero_snapshot() -> None:
    zero = BalanceSnapshot.zero()
    assert (zero.free_usd, zero.bonus_usd, zero.paid_usd, zero.free_requests, zero.version) == (
        Decimal(0),
        Decimal(0),
        Decimal(0),
        0,
        0,
    )


def test_money_serialisation_round_trip() -> None:
    assert money_to_str(Decimal("1E+2")) == "100"
    assert money_to_str(Decimal("0.050000")) == "0.050000"
    for raw in ("0.05", "0.000001", "123456789012.123456"):
        assert money_to_str(str_to_money(raw)) == raw
        assert str_to_money(money_to_str(Decimal(raw))) == Decimal(raw)

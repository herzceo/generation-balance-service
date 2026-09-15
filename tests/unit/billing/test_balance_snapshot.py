from decimal import Decimal

import pytest

from backend.app.shared.ports.billing import (
    BalanceSnapshot,
    micros_to_money,
    money_to_micros,
    money_to_str,
    quantize_money,
    str_to_money,
)


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


def test_micros_round_trip() -> None:
    assert money_to_micros(Decimal(0)) == 0
    assert money_to_micros(Decimal("1E+2")) == 100_000_000
    assert money_to_micros(Decimal("0.000001")) == 1
    for raw in ("0", "0.05", "0.92", "123456789012.123456"):
        assert micros_to_money(money_to_micros(Decimal(raw))) == Decimal(raw)


def test_micros_rejects_amounts_finer_than_the_quantum() -> None:
    with pytest.raises(ValueError, match="finer"):
        money_to_micros(Decimal("0.0000001"))


def test_quantize_money_keeps_six_decimals_unchanged() -> None:
    for raw in ("0", "0.05", "0.000001", "123456789012.123456"):
        assert quantize_money(Decimal(raw)) == Decimal(raw)


def test_quantize_money_rounds_down_beyond_six_decimals() -> None:
    assert quantize_money(Decimal("0.0266666666")) == Decimal("0.026666")
    assert quantize_money(Decimal("0.0000009")) == Decimal(0)
    assert quantize_money(Decimal(1) / Decimal(3)) == Decimal("0.333333")

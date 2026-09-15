from __future__ import annotations

import pytest

from backend.internal.option import Option


def test_some_returns_value_when_present() -> None:
    option: Option[str] = Option("hello")
    assert option.some(ValueError("missing")) == "hello"


def test_some_raises_when_none() -> None:
    option: Option[str] = Option(None)
    with pytest.raises(ValueError, match="missing"):
        option.some(ValueError("missing"))


def test_some_raises_class_when_none() -> None:
    option: Option[int] = Option(None)
    with pytest.raises(LookupError):
        option.some(LookupError)

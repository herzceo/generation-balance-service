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


def test_none_raises_when_value_present() -> None:
    option: Option[str] = Option("exists")
    with pytest.raises(RuntimeError, match="already exists"):
        option.none(RuntimeError("already exists"))


def test_none_does_not_raise_when_empty() -> None:
    option: Option[str] = Option(None)
    option.none(RuntimeError("should not raise"))


def test_some_or_returns_value_when_present() -> None:
    option: Option[str] = Option("hello")
    assert option.some_or("default") == "hello"


def test_some_or_returns_default_when_none() -> None:
    option: Option[str] = Option(None)
    assert option.some_or("default") == "default"


def test_some_raises_class_when_none() -> None:
    option: Option[int] = Option(None)
    with pytest.raises(LookupError):
        option.some(LookupError)

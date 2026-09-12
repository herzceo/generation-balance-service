# Тестовое задание: генерации и баланс пользователей

## Контекст

Есть бот, в котором пользователь запускает генерации. У каждой генерации есть заранее известная стоимость. Вместо реального AI API используется `FakeGenerationProvider`.

PostgreSQL предназначен для долгосрочного хранения состояния, но частые обращения к нему создают заметную нагрузку. Redis доступен как быстрое хранилище.

Нужно реализовать обработку генераций и хранение балансов так, чтобы основная часть работы выполнялась без постоянных чтений и записей PostgreSQL.

## Предоставленный код

Следующий модуль считается частью условия задачи. Его публичные контракты и логику менять нельзя.

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from multiprocessing.managers import SyncManager
from typing import Any, Protocol
from uuid import UUID


class AccessType(Enum):
    FREE = "FREE"
    PAID_ONLY = "PAID_ONLY"
    CONDITIONAL_FREE = "CONDITIONAL_FREE"


class DebitPolicy(Enum):
    DEFAULT = "DEFAULT"
    PAID_ONLY = "PAID_ONLY"
    PAID_THEN_FREE_THEN_BONUS = "PAID_THEN_FREE_THEN_BONUS"


class BalanceView(Protocol):
    """Минимальное read-only представление баланса для политики."""

    free_usd: Decimal
    bonus_usd: Decimal
    paid_usd: Decimal
    free_requests: int


@dataclass(frozen=True, slots=True)
class GenerationModel:
    name: str
    access_type: AccessType
    free_cost_usd: Decimal
    paid_cost_usd: Decimal


MODELS: dict[str, GenerationModel] = {
    "basic": GenerationModel(
        name="basic",
        access_type=AccessType.FREE,
        free_cost_usd=Decimal("0.05"),
        paid_cost_usd=Decimal("0.08"),
    ),
    "premium": GenerationModel(
        name="premium",
        access_type=AccessType.PAID_ONLY,
        free_cost_usd=Decimal("0.20"),
        paid_cost_usd=Decimal("0.20"),
    ),
    "conditional": GenerationModel(
        name="conditional",
        access_type=AccessType.CONDITIONAL_FREE,
        free_cost_usd=Decimal("0.07"),
        paid_cost_usd=Decimal("0.12"),
    ),
}


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    user_id: UUID
    dialog_id: UUID
    client_request_id: UUID
    model_name: str
    conditional_free_eligible: bool = False


@dataclass(frozen=True, slots=True)
class BalanceTopUp:
    operation_id: UUID
    user_id: UUID
    paid_usd: Decimal = Decimal("0")
    bonus_usd: Decimal = Decimal("0")
    free_requests: int = 0


@dataclass(frozen=True, slots=True)
class DebitPlan:
    free_usd: Decimal = Decimal("0")
    bonus_usd: Decimal = Decimal("0")
    paid_usd: Decimal = Decimal("0")
    free_requests: int = 0

    @property
    def total_usd(self) -> Decimal:
        return self.free_usd + self.bonus_usd + self.paid_usd


@dataclass(frozen=True, slots=True)
class Authorization:
    model: GenerationModel
    debit_policy: DebitPolicy
    estimated_cost_usd: Decimal
    debit_plan: DebitPlan


@dataclass(frozen=True, slots=True)
class GenerationResult:
    client_request_id: UUID
    content: str
    billed_cost_usd: Decimal


class GenerationAccessError(Exception):
    pass


class InsufficientBalanceError(GenerationAccessError):
    pass


class FreeRequestsExhaustedError(GenerationAccessError):
    pass


class ConditionalFreeAccessDeniedError(GenerationAccessError):
    pass


class DebitPolicyService:
    """Готовая политика допуска и разбивки предварительного списания."""

    def authorize(
        self,
        request: GenerationRequest,
        balance: BalanceView,
    ) -> Authorization:
        model = MODELS[request.model_name]
        self._validate_balance(balance)

        has_paid_balance = balance.paid_usd > 0
        debit_policy = DebitPolicy.DEFAULT
        estimated_cost_usd = model.free_cost_usd
        consumes_free_request = not has_paid_balance

        if model.access_type is AccessType.PAID_ONLY:
            debit_policy = DebitPolicy.PAID_ONLY
            estimated_cost_usd = model.paid_cost_usd
            consumes_free_request = False
        elif model.access_type is AccessType.CONDITIONAL_FREE:
            if has_paid_balance and balance.paid_usd >= model.paid_cost_usd:
                debit_policy = DebitPolicy.PAID_ONLY
                estimated_cost_usd = model.paid_cost_usd
                consumes_free_request = False
            elif request.conditional_free_eligible:
                debit_policy = (
                    DebitPolicy.PAID_THEN_FREE_THEN_BONUS
                    if has_paid_balance
                    else DebitPolicy.DEFAULT
                )
                estimated_cost_usd = model.free_cost_usd
                consumes_free_request = True
            else:
                raise ConditionalFreeAccessDeniedError()
        elif has_paid_balance:
            estimated_cost_usd = model.paid_cost_usd
            consumes_free_request = False

        if consumes_free_request and balance.free_requests <= 0:
            raise FreeRequestsExhaustedError()

        debit_plan = self._build_plan(
            balance=balance,
            amount=estimated_cost_usd,
            policy=debit_policy,
            consumes_free_request=consumes_free_request,
        )
        return Authorization(
            model=model,
            debit_policy=debit_policy,
            estimated_cost_usd=estimated_cost_usd,
            debit_plan=debit_plan,
        )

    @staticmethod
    def _build_plan(
        *,
        balance: BalanceView,
        amount: Decimal,
        policy: DebitPolicy,
        consumes_free_request: bool,
    ) -> DebitPlan:
        available = {
            "free": balance.free_usd,
            "bonus": balance.bonus_usd,
            "paid": balance.paid_usd,
        }
        if policy is DebitPolicy.PAID_ONLY:
            order = ("paid",)
        elif policy is DebitPolicy.PAID_THEN_FREE_THEN_BONUS:
            order = ("paid", "free", "bonus")
        else:
            order = ("free", "bonus", "paid")

        remaining = amount
        used = {"free": Decimal("0"), "bonus": Decimal("0"), "paid": Decimal("0")}
        for bucket in order:
            taken = min(available[bucket], remaining)
            used[bucket] = taken
            remaining -= taken
            if remaining == 0:
                break

        if remaining > 0:
            raise InsufficientBalanceError()

        return DebitPlan(
            free_usd=used["free"],
            bonus_usd=used["bonus"],
            paid_usd=used["paid"],
            free_requests=int(consumes_free_request),
        )

    @staticmethod
    def _validate_balance(balance: BalanceView) -> None:
        if (
            balance.free_usd < 0
            or balance.bonus_usd < 0
            or balance.paid_usd < 0
            or balance.free_requests < 0
        ):
            raise ValueError("Balance values must be non-negative")


@dataclass(frozen=True, slots=True)
class ProviderScenario:
    delay_seconds: float = 0.1
    fail: bool = False


@dataclass(frozen=True, slots=True)
class ProviderStats:
    calls: dict[UUID, int]
    active_calls: int
    max_active_calls: int


@dataclass(frozen=True, slots=True)
class SharedProviderState:
    scenarios: Any
    calls: Any
    active_calls: Any
    max_active_calls: Any
    lock: Any


def create_shared_provider_state(manager: SyncManager) -> SharedProviderState:
    """Создаёт состояние, которое можно передать нескольким процессам."""

    return SharedProviderState(
        scenarios=manager.dict(),
        calls=manager.dict(),
        active_calls=manager.Value("i", 0),
        max_active_calls=manager.Value("i", 0),
        lock=manager.Lock(),
    )


@dataclass(slots=True)
class FakeGenerationProvider:
    """Общий для процессов тестовый провайдер без реальной AI-генерации."""

    shared: SharedProviderState
    default_scenario: ProviderScenario = field(default_factory=ProviderScenario)

    def set_scenario(
        self,
        client_request_id: UUID,
        scenario: ProviderScenario,
    ) -> None:
        self.shared.scenarios[str(client_request_id)] = scenario

    def get_stats(self) -> ProviderStats:
        with self.shared.lock:
            return ProviderStats(
                calls={UUID(key): value for key, value in self.shared.calls.items()},
                active_calls=self.shared.active_calls.get(),
                max_active_calls=self.shared.max_active_calls.get(),
            )

    async def generate(
        self,
        request: GenerationRequest,
        *,
        authorized_cost_usd: Decimal,
    ) -> GenerationResult:
        request_key = str(request.client_request_id)
        scenario = self.shared.scenarios.get(
            request_key,
            self.default_scenario,
        )
        with self.shared.lock:
            self.shared.calls[request_key] = self.shared.calls.get(request_key, 0) + 1
            active_calls = self.shared.active_calls.get() + 1
            self.shared.active_calls.set(active_calls)
            self.shared.max_active_calls.set(
                max(self.shared.max_active_calls.get(), active_calls)
            )
        try:
            await asyncio.sleep(scenario.delay_seconds)
            if scenario.fail:
                raise RuntimeError("generation failed")
            return GenerationResult(
                client_request_id=request.client_request_id,
                content=f"generated:{request.model_name}",
                billed_cost_usd=authorized_cost_usd,
            )
        finally:
            with self.shared.lock:
                self.shared.active_calls.set(self.shared.active_calls.get() - 1)
```

## Задача

Нужно самостоятельно реализовать:

- модель баланса пользователя;
- хранение состояния в Redis и PostgreSQL;
- обработчик `execute_generation(request: GenerationRequest) -> GenerationResult`;
- обработчик `apply_top_up(command: BalanceTopUp) -> None`;
- применение результата `DebitPolicyService` к балансу;
- необходимые фоновые процессы;
- автоматические тесты.

Один пользователь может одновременно запускать генерации в разных диалогах. В систему могут одновременно прийти 30 запросов одного пользователя с разными `client_request_id`.

Приложение может быть запущено в нескольких процессах на одной машине. Параллельные запросы одного пользователя могут попасть в разные процессы. Распределённый запуск на нескольких машинах учитывать не требуется.

Операции генерации и пополнения могут поступать одновременно. Баланс пользователя может находиться в PostgreSQL и отсутствовать в Redis в момент поступления запросов.

Одинаковый `client_request_id` означает повторную доставку одной операции, в том числе пока первый вызов ещё выполняется.

Решение будет проверяться на одиночных и параллельных запросах, повторной доставке одного `client_request_id` и ошибках провайдера. Оно должно сохранять корректный баланс, не запускать неподтверждённые политикой генерации и не тратить больше доступного. При наличии средств на несколько запросов соответствующие вызовы провайдера должны иметь возможность выполняться параллельно.

После завершения обработки Redis и PostgreSQL должны приходить к согласованному состоянию, а количество обращений к PostgreSQL должно быть существенно меньше количества генераций. Остальные детали реализации являются частью задания.

## Технические требования

- Python 3.12 или новее;
- асинхронная обработка;
- Redis и PostgreSQL;
- `pytest`;
- воспроизводимый локальный запуск;
- нельзя заменять `Decimal` на `float` для денежных значений.

HTTP API делать необязательно. Достаточно application-сервиса, вызываемого из тестов.

## Что предоставить

- исходный код;
- схему или миграции PostgreSQL;
- автоматические тесты;
- инструкцию по запуску;
- короткое описание принятого решения и его ограничений;
- описание того, как проверялась корректность решения и снижение числа обращений к PostgreSQL.

Ориентир по времени выполнения — 6–10 часов. Если часть решения не завершена, опишите, как бы вы её реализовали при наличии дополнительного времени.

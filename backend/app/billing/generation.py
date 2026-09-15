from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic, time

from backend.app.billing.balance_loader import BalanceLoader
from backend.app.billing.config import BillingConfig
from backend.app.billing.refund import ReservationRefunder
from backend.app.errors import (
    BalanceContentionError,
    GenerationFailedError,
    GenerationInProgressError,
    InvalidInputError,
)
from backend.app.shared.ports.billing.balance_store import (
    AlreadyApplied,
    Applied,
    BalanceStore,
    Conflict,
    Duplicate,
    GenerationRecord,
    GenerationStatus,
    Missing,
    Reserved,
    Stale,
    quantize_money,
    surplus_refund,
)
from backend.app.shared.ports.billing.generation_provider import GenerationProvider
from backend.domain.generation import (
    MODELS,
    Authorization,
    DebitPolicyService,
    GenerationAccessError,
    GenerationRequest,
    GenerationResult,
)

logger = logging.getLogger(__name__)

_JITTER_AFTER_ATTEMPT = 5


@dataclass
class GenerationService:
    store: BalanceStore
    provider: GenerationProvider
    loader: BalanceLoader
    refunder: ReservationRefunder
    config: BillingConfig
    policy: DebitPolicyService

    async def execute_generation(self, request: GenerationRequest) -> GenerationResult:
        if request.model_name not in MODELS:
            raise InvalidInputError(message=f"unknown model {request.model_name!r}")
        reserved = await self._reserve(request)
        if isinstance(reserved, GenerationResult):
            return reserved
        authorization = reserved
        try:
            async with asyncio.timeout(self.config.PROVIDER_TIMEOUT_SECONDS):
                result = await self.provider.generate(
                    request, authorized_cost_usd=authorization.estimated_cost_usd
                )
        except BaseException as exc:
            reason = str(exc) or type(exc).__name__
            await self.refunder.refund(
                user_id=request.user_id,
                client_request_id=request.client_request_id,
                plan=authorization.debit_plan,
                error=reason,
            )
            if isinstance(exc, Exception):
                raise GenerationFailedError(message=reason) from exc
            raise
        charged = await self._settle(request, authorization, result)
        return GenerationResult(
            client_request_id=request.client_request_id,
            content=result.content,
            billed_cost_usd=charged,
        )

    async def _reserve(self, request: GenerationRequest) -> Authorization | GenerationResult:
        """Authorize against the current snapshot and debit it atomically; retry on CAS conflict."""
        snapshot = await self.loader.ensure_loaded(request.user_id)
        for attempt in range(self.config.RESERVE_MAX_ATTEMPTS):
            try:
                authorization = self.policy.authorize(request, snapshot)
            except GenerationAccessError as denied:
                resolved = await self._denied_or_duplicate(request, denied)
                if resolved is not None:
                    return resolved
                snapshot = await self.loader.ensure_loaded(request.user_id)
                continue
            outcome = await self.store.reserve(
                request=request,
                expected_version=snapshot.version,
                plan=authorization.debit_plan,
                authorized_cost_usd=authorization.estimated_cost_usd,
                started_at=time(),
            )
            match outcome:
                case Reserved():
                    return authorization
                case Conflict(current=current):
                    snapshot = current
                    if attempt >= _JITTER_AFTER_ATTEMPT:
                        await asyncio.sleep(random.uniform(0, 0.002))
                case Missing():
                    snapshot = await self.loader.ensure_loaded(request.user_id)
                case Duplicate(record=record):
                    resolved = await self._resolve_duplicate(request, record)
                    if resolved is not None:
                        return resolved
                    snapshot = await self.loader.ensure_loaded(request.user_id)
        raise BalanceContentionError(message="reservation retries exhausted")

    async def _denied_or_duplicate(
        self, request: GenerationRequest, denied: GenerationAccessError
    ) -> GenerationResult | None:
        """A redelivery keeps its cached outcome even if the balance has since dropped."""
        record = (await self.store.get_generation(request.client_request_id)).value
        if record is None:
            raise denied
        return await self._resolve_duplicate(request, record)

    async def _resolve_duplicate(
        self, request: GenerationRequest, record: GenerationRecord
    ) -> GenerationResult | None:
        if (record.user_id, record.dialog_id, record.model_name) != (
            request.user_id,
            request.dialog_id,
            request.model_name,
        ):
            raise InvalidInputError(message="client_request_id reused with a different payload")
        deadline = monotonic() + self.config.RUNNING_WAIT_TIMEOUT_SECONDS
        while True:
            if record.status is GenerationStatus.DONE:
                return GenerationResult(
                    client_request_id=request.client_request_id,
                    content=record.content or "",
                    billed_cost_usd=record.billed_cost_usd or record.authorized_cost_usd,
                )
            if record.status is GenerationStatus.FAILED:
                raise GenerationFailedError(message=record.error or "generation failed")
            if monotonic() > deadline:
                raise GenerationInProgressError(message="generation still running")
            await asyncio.sleep(self.config.RUNNING_POLL_INTERVAL_SECONDS)
            fresh = (await self.store.get_generation(request.client_request_id)).value
            if fresh is None:
                return None
            record = fresh

    async def _settle(
        self, request: GenerationRequest, authorization: Authorization, result: GenerationResult
    ) -> Decimal:
        """Mark the record done and refund the unbilled part of the plan; returns the charge."""
        plan = authorization.debit_plan
        charged = quantize_money(max(Decimal(0), min(result.billed_cost_usd, plan.total_usd)))
        if not Decimal(0) <= result.billed_cost_usd <= plan.total_usd:
            logger.warning(
                "generation %s billed %s outside [0, authorized %s]; charging the clamped amount",
                request.client_request_id,
                result.billed_cost_usd,
                plan.total_usd,
            )
        refund = surplus_refund(plan, authorization.debit_policy, charged)
        for _ in range(self.config.RESERVE_MAX_ATTEMPTS):
            outcome = await self.store.settle(
                user_id=request.user_id,
                client_request_id=request.client_request_id,
                content=result.content,
                billed_cost_usd=charged,
                provider_billed_cost_usd=result.billed_cost_usd,
                refund=refund,
            )
            match outcome:
                case Applied() | AlreadyApplied():
                    return charged
                case Stale():
                    raise GenerationFailedError(message="reservation was reaped before settle")
                case Missing():
                    await self.loader.ensure_loaded(request.user_id)
        raise BalanceContentionError(message="settle retries exhausted")

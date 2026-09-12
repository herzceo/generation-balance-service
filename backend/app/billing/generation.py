from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from time import monotonic, time

from backend.app.billing.balance_loader import BalanceLoader
from backend.app.billing.config import BillingConfig
from backend.app.errors import (
    BalanceContentionError,
    GenerationFailedError,
    GenerationInProgressError,
    InvalidInputError,
)
from backend.app.shared.ports.billing.balance_store import (
    Applied,
    BalanceStore,
    Conflict,
    Duplicate,
    GenerationRecord,
    GenerationStatus,
    Missing,
    Reserved,
    Stale,
)
from backend.app.shared.ports.billing.generation_provider import GenerationProvider
from backend.domain.generation import (
    MODELS,
    Authorization,
    DebitPlan,
    DebitPolicyService,
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
            result = await self.provider.generate(
                request, authorized_cost_usd=authorization.estimated_cost_usd
            )
        except BaseException as exc:
            await self._refund(
                request, authorization.debit_plan, error=str(exc) or type(exc).__name__
            )
            if isinstance(exc, Exception):
                raise GenerationFailedError(message=str(exc)) from exc
            raise
        settled = await self.store.settle(
            request.client_request_id,
            content=result.content,
            billed_cost_usd=result.billed_cost_usd,
        )
        if not settled:
            logger.warning(
                "generation %s settled without a running record; charge stands",
                request.client_request_id,
            )
        return result

    async def _reserve(self, request: GenerationRequest) -> Authorization | GenerationResult:
        """Authorize against the current snapshot and debit it atomically; retry on CAS conflict."""
        snapshot = await self.loader.ensure_loaded(request.user_id)
        for attempt in range(self.config.RESERVE_MAX_ATTEMPTS):
            authorization = self.policy.authorize(request, snapshot)
            plan = authorization.debit_plan
            outcome = await self.store.reserve(
                request=request,
                expected_version=snapshot.version,
                new=snapshot.apply_plan(plan),
                plan=plan,
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

    async def _refund(self, request: GenerationRequest, plan: DebitPlan, *, error: str) -> None:
        snapshot = await self.loader.ensure_loaded(request.user_id)
        for _ in range(self.config.RESERVE_MAX_ATTEMPTS):
            outcome = await self.store.refund(
                user_id=request.user_id,
                client_request_id=request.client_request_id,
                expected_version=snapshot.version,
                new=snapshot.refund_plan(plan),
                error=error,
            )
            match outcome:
                case Applied() | Stale():
                    return
                case Conflict(current=current):
                    snapshot = current
                case Missing():
                    snapshot = await self.loader.ensure_loaded(request.user_id)
        raise BalanceContentionError(message="refund retries exhausted")

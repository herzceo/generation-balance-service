from abc import abstractmethod
from decimal import Decimal
from typing import Protocol

from backend.domain.generation import GenerationRequest, GenerationResult


class GenerationProvider(Protocol):
    @abstractmethod
    async def generate(
        self,
        request: GenerationRequest,
        *,
        authorized_cost_usd: Decimal,
    ) -> GenerationResult: ...

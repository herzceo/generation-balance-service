from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UUID as SQL_UUID

from .base import Base


class Balance(Base):
    user_id: Mapped[UUID] = mapped_column(SQL_UUID(as_uuid=True), primary_key=True)
    free_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, server_default=text("0")
    )
    bonus_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, server_default=text("0")
    )
    paid_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, server_default=text("0")
    )
    free_requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

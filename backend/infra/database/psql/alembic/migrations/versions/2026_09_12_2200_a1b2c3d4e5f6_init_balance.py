"""init balance

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-09-12 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "balance",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("free_usd", sa.Numeric(18, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("bonus_usd", sa.Numeric(18, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("paid_usd", sa.Numeric(18, 6), server_default=sa.text("0"), nullable=False),
        sa.Column("free_requests", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_balance")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("balance")

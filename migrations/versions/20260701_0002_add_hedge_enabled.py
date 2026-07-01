"""Add Hedge Mode validation state to exchange credentials.

Revision ID: 20260701_0002
Revises: 20260627_0001
Create Date: 2026-07-01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0002"
down_revision: str | Sequence[str] | None = "20260627_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exchange_credentials",
        sa.Column(
            "hedge_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("exchange_credentials", "hedge_enabled")

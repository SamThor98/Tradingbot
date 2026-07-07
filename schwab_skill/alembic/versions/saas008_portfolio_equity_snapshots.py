"""Add portfolio equity snapshots.

Revision ID: saas008
Revises: saas007
Create Date: 2026-07-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "saas008"
down_revision: Union[str, Sequence[str], None] = "saas007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "portfolio_equity_snapshots" in insp.get_table_names():
        return
    op.create_table(
        "portfolio_equity_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=128), nullable=False, server_default="local"),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=True),
        sa.Column("positions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="schwab"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "snapshot_date", name="uq_portfolio_equity_snapshot_user_date"),
    )
    op.create_index(
        "ix_portfolio_equity_snapshots_user_date",
        "portfolio_equity_snapshots",
        ["user_id", "snapshot_date"],
        unique=False,
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "portfolio_equity_snapshots" not in insp.get_table_names():
        return
    op.drop_index("ix_portfolio_equity_snapshots_user_date", table_name="portfolio_equity_snapshots")
    op.drop_table("portfolio_equity_snapshots")

"""Backfill pending_trades.user_id for tenant isolation.

Revision ID: saas004
Revises: saas003
Create Date: 2026-04-10

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "saas004"
down_revision: Union[str, Sequence[str], None] = "saas003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LOCAL_USER = "local"


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "pending_trades" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("pending_trades")}
    if "user_id" not in cols:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            with op.batch_alter_table("pending_trades", schema=None) as batch:
                batch.add_column(sa.Column("user_id", sa.String(length=128), nullable=True))
        else:
            op.add_column("pending_trades", sa.Column("user_id", sa.String(length=128), nullable=True))
        try:
            op.create_index("ix_pending_trades_user_id", "pending_trades", ["user_id"], unique=False)
        except Exception:
            pass
    conn.execute(
        sa.text("UPDATE pending_trades SET user_id = :uid WHERE user_id IS NULL"),
        {"uid": _LOCAL_USER},
    )


def downgrade() -> None:
    pass

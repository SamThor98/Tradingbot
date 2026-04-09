"""Backtest runs for user strategy experiments.

Revision ID: saas003
Revises: saas002
Create Date: 2026-04-09

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "saas003"
down_revision: Union[str, Sequence[str], None] = "saas002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "backtest_runs" not in tables:
        op.create_table(
            "backtest_runs",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("celery_task_id", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("spec_json", sa.Text(), nullable=False),
            sa.Column("result_json", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_backtest_runs_user_id", "backtest_runs", ["user_id"], unique=False)
        op.create_index("ix_backtest_runs_celery_task_id", "backtest_runs", ["celery_task_id"], unique=False)
        op.create_index("ix_backtest_runs_status", "backtest_runs", ["status"], unique=False)
        op.create_index("ix_backtest_runs_user_created", "backtest_runs", ["user_id", "created_at"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "backtest_runs" in insp.get_table_names():
        op.drop_index("ix_backtest_runs_user_created", table_name="backtest_runs")
        op.drop_index("ix_backtest_runs_status", table_name="backtest_runs")
        op.drop_index("ix_backtest_runs_celery_task_id", table_name="backtest_runs")
        op.drop_index("ix_backtest_runs_user_id", table_name="backtest_runs")
        op.drop_table("backtest_runs")

"""Add event-probability tables (normalized events + append-only history).

Revision ID: saas010
Revises: saas009
Create Date: 2026-08-27

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "saas010"
down_revision: Union[str, Sequence[str], None] = "saas009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "event_probability_events" not in tables:
        op.create_table(
            "event_probability_events",
            sa.Column("event_id", sa.String(length=160), primary_key=True),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("source_market_id", sa.String(length=128), nullable=False),
            sa.Column("event_name", sa.Text(), nullable=False, server_default=""),
            sa.Column("event_category", sa.String(length=48), nullable=False, server_default="other"),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("resolution_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("current_probability", sa.Float(), nullable=False),
            sa.Column("adjusted_probability", sa.Float(), nullable=False),
            sa.Column("previous_probability", sa.Float(), nullable=True),
            sa.Column("probability_change_1d", sa.Float(), nullable=True),
            sa.Column("probability_change_7d", sa.Float(), nullable=True),
            sa.Column("probability_change_30d", sa.Float(), nullable=True),
            sa.Column("volume", sa.Float(), nullable=True),
            sa.Column("liquidity", sa.Float(), nullable=True),
            sa.Column("spread", sa.Float(), nullable=True),
            sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolution_status", sa.String(length=24), nullable=False, server_default="open"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_event_probability_events_source", "event_probability_events", ["source"])
        op.create_index("ix_event_probability_events_event_category", "event_probability_events", ["event_category"])
        op.create_index("ix_event_probability_events_last_updated", "event_probability_events", ["last_updated"])
        op.create_index(
            "ix_event_probability_events_resolution_status",
            "event_probability_events",
            ["resolution_status"],
        )
        op.create_index(
            "ix_event_probability_events_source_market",
            "event_probability_events",
            ["source", "source_market_id"],
        )

    if "event_probability_history" not in tables:
        # Append-only: the unique constraint makes a repeated ingest at the same
        # timestamp a no-op instead of a duplicate observation that would
        # distort probability-change lookbacks.
        op.create_table(
            "event_probability_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("event_id", sa.String(length=160), nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("raw_probability", sa.Float(), nullable=False),
            sa.Column("adjusted_probability", sa.Float(), nullable=False),
            sa.Column("volume", sa.Float(), nullable=True),
            sa.Column("liquidity", sa.Float(), nullable=True),
            sa.Column("spread", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["event_id"],
                ["event_probability_events.event_id"],
                name="fk_event_probability_history_event",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("event_id", "timestamp", name="uq_event_probability_history_event_ts"),
        )
        op.create_index(
            "ix_event_probability_history_event_ts",
            "event_probability_history",
            ["event_id", "timestamp"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "event_probability_history" in tables:
        op.drop_index("ix_event_probability_history_event_ts", table_name="event_probability_history")
        op.drop_table("event_probability_history")

    if "event_probability_events" in tables:
        for index_name in (
            "ix_event_probability_events_source_market",
            "ix_event_probability_events_resolution_status",
            "ix_event_probability_events_last_updated",
            "ix_event_probability_events_event_category",
            "ix_event_probability_events_source",
        ):
            op.drop_index(index_name, table_name="event_probability_events")
        op.drop_table("event_probability_events")

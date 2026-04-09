"""Stripe subscription columns and webhook idempotency table.

Revision ID: saas002
Revises: saas001
Create Date: 2026-04-08

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "saas002"
down_revision: Union[str, Sequence[str], None] = "saas001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    dialect = conn.dialect.name
    tables = set(insp.get_table_names())

    if "stripe_webhook_events" not in tables:
        op.create_table(
            "stripe_webhook_events",
            sa.Column("id", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "users" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("users")}
        add_customer = "stripe_customer_id" not in cols
        add_sub = "stripe_subscription_id" not in cols
        add_status = "subscription_status" not in cols
        add_end = "subscription_current_period_end" not in cols
        if add_customer or add_sub or add_status or add_end:
            if dialect == "sqlite":
                with op.batch_alter_table("users", schema=None) as batch:
                    if add_customer:
                        batch.add_column(sa.Column("stripe_customer_id", sa.String(length=64), nullable=True))
                    if add_sub:
                        batch.add_column(sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True))
                    if add_status:
                        batch.add_column(sa.Column("subscription_status", sa.String(length=32), nullable=True))
                    if add_end:
                        batch.add_column(
                            sa.Column("subscription_current_period_end", sa.DateTime(timezone=True), nullable=True)
                        )
            else:
                if add_customer:
                    op.add_column("users", sa.Column("stripe_customer_id", sa.String(length=64), nullable=True))
                if add_sub:
                    op.add_column("users", sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True))
                if add_status:
                    op.add_column("users", sa.Column("subscription_status", sa.String(length=32), nullable=True))
                if add_end:
                    op.add_column(
                        "users",
                        sa.Column("subscription_current_period_end", sa.DateTime(timezone=True), nullable=True),
                    )

    if "users" in insp.get_table_names():
        insp2 = sa.inspect(conn)
        ix_names = {ix["name"] for ix in insp2.get_indexes("users")}
        if "ix_users_stripe_customer_id" not in ix_names:
            op.create_index("ix_users_stripe_customer_id", "users", ["stripe_customer_id"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    insp = sa.inspect(conn)

    if "users" in insp.get_table_names():
        ix_names = {ix["name"] for ix in insp.get_indexes("users")}
        if "ix_users_stripe_customer_id" in ix_names:
            op.drop_index("ix_users_stripe_customer_id", table_name="users")

    if "users" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("users")}
        if dialect == "sqlite":
            with op.batch_alter_table("users", schema=None) as batch:
                if "subscription_current_period_end" in cols:
                    batch.drop_column("subscription_current_period_end")
                if "subscription_status" in cols:
                    batch.drop_column("subscription_status")
                if "stripe_subscription_id" in cols:
                    batch.drop_column("stripe_subscription_id")
                if "stripe_customer_id" in cols:
                    batch.drop_column("stripe_customer_id")
        else:
            if "subscription_current_period_end" in cols:
                op.drop_column("users", "subscription_current_period_end")
            if "subscription_status" in cols:
                op.drop_column("users", "subscription_status")
            if "stripe_subscription_id" in cols:
                op.drop_column("users", "stripe_subscription_id")
            if "stripe_customer_id" in cols:
                op.drop_column("users", "stripe_customer_id")

    if "stripe_webhook_events" in insp.get_table_names():
        op.drop_table("stripe_webhook_events")

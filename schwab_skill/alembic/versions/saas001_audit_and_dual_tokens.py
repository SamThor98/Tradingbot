"""Audit log, dual OAuth payloads, scan index.

Revision ID: saas001
Revises:
Create Date: 2026-04-08

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "saas001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "audit_logs" not in tables:
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=True),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("detail_json", sa.Text(), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"], unique=False)
        op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
        op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"], unique=False)
        op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)

    if "user_credentials" in tables:
        cols = {c["name"] for c in insp.get_columns("user_credentials")}
        dialect = conn.dialect.name
        add_market = "market_token_payload_enc" not in cols
        add_account = "account_token_payload_enc" not in cols
        if add_market or add_account:
            if dialect == "sqlite":
                with op.batch_alter_table("user_credentials", schema=None) as batch:
                    if add_market:
                        batch.add_column(sa.Column("market_token_payload_enc", sa.Text(), nullable=True))
                    if add_account:
                        batch.add_column(sa.Column("account_token_payload_enc", sa.Text(), nullable=True))
            else:
                if add_market:
                    op.add_column(
                        "user_credentials",
                        sa.Column("market_token_payload_enc", sa.Text(), nullable=True),
                    )
                if add_account:
                    op.add_column(
                        "user_credentials",
                        sa.Column("account_token_payload_enc", sa.Text(), nullable=True),
                    )

    if "scan_results" in tables:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_scan_results_user_created "
            "ON scan_results (user_id, created_at)"
        )


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    op.execute(sa.text("DROP INDEX IF EXISTS ix_scan_results_user_created"))

    insp = sa.inspect(conn)
    if "user_credentials" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("user_credentials")}
        if dialect == "sqlite":
            with op.batch_alter_table("user_credentials", schema=None) as batch:
                if "market_token_payload_enc" in cols:
                    batch.drop_column("market_token_payload_enc")
                if "account_token_payload_enc" in cols:
                    batch.drop_column("account_token_payload_enc")
        else:
            if "market_token_payload_enc" in cols:
                op.drop_column("user_credentials", "market_token_payload_enc")
            if "account_token_payload_enc" in cols:
                op.drop_column("user_credentials", "account_token_payload_enc")

    insp = sa.inspect(conn)
    if "audit_logs" in insp.get_table_names():
        op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
        op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
        op.drop_index("ix_audit_logs_action", table_name="audit_logs")
        op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
        op.drop_table("audit_logs")

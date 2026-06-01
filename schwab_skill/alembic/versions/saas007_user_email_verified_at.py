"""Add users.email_verified_at (one-time email verification stamp).

Revision ID: saas007
Revises: saas006
Create Date: 2026-06-01

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "saas007"
down_revision: Union[str, Sequence[str], None] = "saas006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    insp = sa.inspect(conn)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "email_verified_at" in cols:
        return
    if dialect == "sqlite":
        with op.batch_alter_table("users", schema=None) as batch:
            batch.add_column(
                sa.Column(
                    "email_verified_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )
    else:
        op.add_column(
            "users",
            sa.Column(
                "email_verified_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    insp = sa.inspect(conn)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "email_verified_at" not in cols:
        return
    if dialect == "sqlite":
        with op.batch_alter_table("users", schema=None) as batch:
            batch.drop_column("email_verified_at")
    else:
        op.drop_column("users", "email_verified_at")

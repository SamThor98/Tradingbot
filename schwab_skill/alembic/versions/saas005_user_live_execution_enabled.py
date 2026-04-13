"""Add users.live_execution_enabled (default off).

Revision ID: saas005
Revises: saas004
Create Date: 2026-04-10

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "saas005"
down_revision: Union[str, Sequence[str], None] = "saas004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == "sqlite":
        insp = sa.inspect(conn)
        if "users" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("users")}
        if "live_execution_enabled" in cols:
            return
        with op.batch_alter_table("users", schema=None) as batch:
            batch.add_column(
                sa.Column(
                    "live_execution_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
    elif dialect == "postgresql":
        # ADD COLUMN IF NOT EXISTS uses the same unqualified name resolution as
        # plain ALTER TABLE, avoiding false negatives from Inspector vs. actual
        # target table (search_path / multiple "users" / race with other writers).
        op.execute(
            sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS live_execution_enabled BOOLEAN DEFAULT false NOT NULL")
        )
    else:
        insp = sa.inspect(conn)
        if "users" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("users")}
        if "live_execution_enabled" in cols:
            return
        op.add_column(
            "users",
            sa.Column(
                "live_execution_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("users", schema=None) as batch:
            batch.drop_column("live_execution_enabled")
    elif dialect == "postgresql":
        op.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS live_execution_enabled"))
    else:
        op.drop_column("users", "live_execution_enabled")

"""Add Book feature tables (tax prefs, journal).

Revision ID: saas009
Revises: saas008
Create Date: 2026-07-16

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "saas009"
down_revision: Union[str, Sequence[str], None] = "saas008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    if "book_tax_prefs" not in tables:
        op.create_table(
            "book_tax_prefs",
            sa.Column("user_id", sa.String(length=128), primary_key=True),
            sa.Column("federal_st_rate", sa.Float(), nullable=True),
            sa.Column("federal_lt_rate", sa.Float(), nullable=True),
            sa.Column("state_rate", sa.Float(), nullable=True),
            sa.Column("tax_year", sa.Integer(), nullable=True),
            sa.Column("rates_configured", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "book_journal_tickers" not in tables:
        op.create_table(
            "book_journal_tickers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("symbol", sa.String(length=16), nullable=False),
            sa.Column("thesis_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("user_id", "symbol", name="uq_book_journal_ticker_user_symbol"),
        )
        op.create_index("ix_book_journal_tickers_user", "book_journal_tickers", ["user_id"])

    if "book_journal_notes" not in tables:
        op.create_table(
            "book_journal_notes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("symbol", sa.String(length=16), nullable=False),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="quick"),
            sa.Column("note_type", sa.String(length=32), nullable=False, server_default="other"),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("note_date", sa.Date(), nullable=False),
            sa.Column("fill_activity_id", sa.String(length=64), nullable=True),
            sa.Column("template_json", json_type, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_book_journal_notes_user_symbol",
            "book_journal_notes",
            ["user_id", "symbol"],
        )
        op.create_index(
            "ix_book_journal_notes_user_date",
            "book_journal_notes",
            ["user_id", "note_date"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())
    if "book_journal_notes" in tables:
        op.drop_index("ix_book_journal_notes_user_date", table_name="book_journal_notes")
        op.drop_index("ix_book_journal_notes_user_symbol", table_name="book_journal_notes")
        op.drop_table("book_journal_notes")
    if "book_journal_tickers" in tables:
        op.drop_index("ix_book_journal_tickers_user", table_name="book_journal_tickers")
        op.drop_table("book_journal_tickers")
    if "book_tax_prefs" in tables:
        op.drop_table("book_tax_prefs")

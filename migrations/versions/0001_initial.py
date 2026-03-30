"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-03-24
"""
from alembic import op

from sniper_bot.storage import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)

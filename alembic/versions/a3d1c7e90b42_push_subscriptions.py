"""push subscriptions

Revision ID: a3d1c7e90b42
Revises: f19386697e34
Create Date: 2026-08-23

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a3d1c7e90b42'
down_revision: str | None = 'f19386697e34'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'push_subscriptions',
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('p256dh', sa.String(length=255), nullable=False),
        sa.Column('auth', sa.String(length=255), nullable=False),
        sa.Column('region_code', sa.String(length=10), nullable=True),
        sa.Column('user_agent', sa.String(length=400), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('endpoint', name='uq_push_subscriptions_endpoint'),
    )
    op.create_index('ix_push_subscriptions_region_code', 'push_subscriptions', ['region_code'])


def downgrade() -> None:
    op.drop_index('ix_push_subscriptions_region_code', table_name='push_subscriptions')
    op.drop_table('push_subscriptions')

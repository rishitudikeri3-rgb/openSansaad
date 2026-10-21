"""documents text hash, extractor version, meta

Revision ID: 48e8e5a42dd2
Revises: 9fa0d87b24fb
Create Date: 2026-10-21 14:10:08.536615

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '48e8e5a42dd2'
down_revision: str | None = '9fa0d87b24fb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL until the extraction pipeline (re)writes the row.
    op.add_column('documents', sa.Column('text_sha256', sa.String(length=64), nullable=True))
    op.add_column('documents', sa.Column('extractor_version', sa.String(length=16), nullable=True))
    op.add_column('documents', sa.Column('meta', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'meta')
    op.drop_column('documents', 'extractor_version')
    op.drop_column('documents', 'text_sha256')

"""documents ocr_regions_dropped

Revision ID: 092ecb5d2e51
Revises: 48e8e5a42dd2
Create Date: 2026-10-21 16:18:32.312152

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '092ecb5d2e51'
down_revision: str | None = '48e8e5a42dd2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL until the extraction pipeline (re)writes the row.
    op.add_column('documents', sa.Column('ocr_regions_dropped', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'ocr_regions_dropped')

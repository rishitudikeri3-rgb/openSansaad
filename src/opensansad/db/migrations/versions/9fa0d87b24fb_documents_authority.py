"""documents authority

Revision ID: 9fa0d87b24fb
Revises: 005e8876ef09
Create Date: 2026-10-21 13:43:18.546716

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '9fa0d87b24fb'
down_revision: str | None = '005e8876ef09'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows are all primary sources (PRS / e-Gazette copies, a legislative.gov.in note).
    op.add_column('documents', sa.Column('authority', sa.Enum('primary', 'secondary', name='authority', native_enum=False, create_constraint=False, length=32), server_default='primary', nullable=False))
    op.create_check_constraint(op.f('ck_documents_authority'), 'documents',
                               "authority IN ('primary', 'secondary')")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_documents_authority'), 'documents', type_='check')
    op.drop_column('documents', 'authority')

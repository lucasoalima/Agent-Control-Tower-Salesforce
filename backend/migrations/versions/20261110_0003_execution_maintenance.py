"""execution expiry and reconciliation markers

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions", sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "executions",
        sa.Column("reconciliation_required_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE executions SET approval_expires_at = requested_at + INTERVAL '300 seconds' "
        "WHERE status IN ('PENDING_APPROVAL', 'APPROVED')"
    )


def downgrade() -> None:
    op.drop_column("executions", "reconciliation_required_at")
    op.drop_column("executions", "approval_expires_at")

"""Payment plumbing.

Adds:

* `orders.payout_idempotency_key` (UNIQUE) — the value sent in the
  `X-Payout-Idempotency` header, persisted *before* the RazorpayX call so any
  retry reuses it and cannot create a second payout.
* `orders.payout_attempted_at` — claimed with a conditional UPDATE before the
  call; this is what makes concurrent approvals safe.
* `orders.payout_status`, `orders.failure_reason` — RazorpayX's verbatim status
  and a merchant-readable failure explanation.
* `orders.approved_at` — when a human authorised the spend. The daily spend cap
  is attributed by this, not by creation time.
* `orders.forecast_reasoning`, `orders.supplier_reasoning`,
  `orders.unit_price_paise_at_proposal` — the agents' own words and the price
  they saw, so a historical decision keeps the reasoning it was made with.
* `webhook_events` — one row per delivery, keyed by the unique
  `X-Razorpay-Event-Id`. That UNIQUE constraint is the database-level guarantee
  that a redelivered webhook cannot be processed twice.

Every new column is nullable, so this applies to a database with live orders
without a backfill.

Revision ID: 5bc53891e7eb
Revises: 245c6f606bde
Create Date: 2026-08-27 14:53:54.871985

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import sqlalchemy as sa

revision: str = '5bc53891e7eb'
down_revision: Union[str, None] = '245c6f606bde'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('webhook_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.String(length=128), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('payout_id', sa.String(length=64), nullable=True),
    sa.Column('payout_status', sa.String(length=32), nullable=True),
    sa.Column('related_order_id', sa.Integer(), nullable=True),
    sa.Column('outcome', sa.Enum('applied', 'already_applied', 'acknowledged', 'unknown_payout', 'ignored', 'conflict', name='webhook_outcome', native_enum=False, length=24), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['related_order_id'], ['orders.id'], name=op.f('fk_webhook_events_related_order_id_orders'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_webhook_events'))
    )
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webhook_events_event_id'), ['event_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_webhook_events_event_type'), ['event_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_webhook_events_payout_id'), ['payout_id'], unique=False)
        batch_op.create_index('ix_webhook_events_payout_id_event_type', ['payout_id', 'event_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_webhook_events_received_at'), ['received_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_webhook_events_related_order_id'), ['related_order_id'], unique=False)

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payout_idempotency_key', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('payout_attempted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('payout_status', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('failure_reason', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('forecast_reasoning', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('supplier_reasoning', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('unit_price_paise_at_proposal', sa.BigInteger(), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('uq_orders_payout_idempotency_key'), ['payout_idempotency_key'])



def downgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_orders_payout_idempotency_key'), type_='unique')
        batch_op.drop_column('unit_price_paise_at_proposal')
        batch_op.drop_column('supplier_reasoning')
        batch_op.drop_column('forecast_reasoning')
        batch_op.drop_column('approved_at')
        batch_op.drop_column('failure_reason')
        batch_op.drop_column('payout_status')
        batch_op.drop_column('payout_attempted_at')
        batch_op.drop_column('payout_idempotency_key')

    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_webhook_events_related_order_id'))
        batch_op.drop_index(batch_op.f('ix_webhook_events_received_at'))
        batch_op.drop_index('ix_webhook_events_payout_id_event_type')
        batch_op.drop_index(batch_op.f('ix_webhook_events_payout_id'))
        batch_op.drop_index(batch_op.f('ix_webhook_events_event_type'))
        batch_op.drop_index(batch_op.f('ix_webhook_events_event_id'))

    op.drop_table('webhook_events')

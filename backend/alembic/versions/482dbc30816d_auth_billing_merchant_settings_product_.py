"""Auth, billing, merchant settings, and product selling price.

Adds the tables the supplied design requires:

* `users` / `sessions` — authentication. Sessions are opaque server-side tokens
  (SHA-256 of the token is stored, never the token) rather than JWTs, because a
  system that authorises payouts must be able to revoke a session immediately.
* `plans` / `subscriptions` / `invoices` / `payment_methods` — the design has a
  pricing selector and a billing page. Invoices are issued `due`; nothing here
  ever marks one paid, because no payment gateway is wired up and inventing a
  charge would break the same rule that governs procurement payouts.
  `payment_methods` stores brand / last4 / expiry only, never a card number.
* `merchant_settings` — a singleton row (CHECK id = 1) so the design's editable
  spend limits and automation toggles have somewhere to live. The limit columns
  are NULLABLE: NULL means "not overridden, use the environment", so an existing
  deployment behaves exactly as before until someone changes a value.
* `products.selling_price_paise` — the schema previously held only supplier
  *cost* prices, which made the design's "Sales this month" figure in rupees
  uncomputable. Nullable, so a product without a retail price contributes
  nothing to revenue rather than contributing a guess.

Single shared workspace: no `workspace_id` anywhere. A user is an identity, not
a tenant.

Revision ID: 482dbc30816d
Revises: dfe80c1e476d
Create Date: 2026-08-27 17:10:49.825820

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import sqlalchemy as sa

revision: str = '482dbc30816d'
down_revision: Union[str, None] = 'dfe80c1e476d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('plans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('tagline', sa.String(length=255), nullable=False),
    sa.Column('price_monthly_paise', sa.BigInteger(), nullable=False),
    sa.Column('price_annual_paise', sa.BigInteger(), nullable=False),
    sa.Column('features', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('is_popular', sa.Boolean(), nullable=False),
    sa.Column('cta_label', sa.String(length=64), nullable=False),
    sa.Column('icon', sa.String(length=32), nullable=False, comment='Icon key from the design icon set.'),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('price_annual_paise >= 0', name=op.f('ck_plans_price_annual_non_negative')),
    sa.CheckConstraint('price_monthly_paise >= 0', name=op.f('ck_plans_price_monthly_non_negative')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plans'))
    )
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_plans_key'), ['key'], unique=True)

    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('business_name', sa.String(length=160), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('invoices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('amount_paise', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('due', 'paid', 'void', name='invoice_status', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('plan_key', sa.String(length=32), nullable=False),
    sa.Column('billing_cycle', sa.Enum('monthly', 'annual', name='invoice_billing_cycle', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.CheckConstraint('amount_paise >= 0', name=op.f('ck_invoices_invoice_amount_non_negative')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_invoices_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invoices'))
    )
    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invoices_issued_at'), ['issued_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_invoices_number'), ['number'], unique=True)
        batch_op.create_index(batch_op.f('ix_invoices_user_id'), ['user_id'], unique=False)

    op.create_table('merchant_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('max_order_spend_paise', sa.BigInteger(), nullable=True),
    sa.Column('max_daily_spend_paise', sa.BigInteger(), nullable=True),
    sa.Column('max_reorder_quantity', sa.Integer(), nullable=True),
    sa.Column('auto_check_enabled', sa.Boolean(), nullable=False),
    sa.Column('notify_on_proposal', sa.Boolean(), nullable=False),
    sa.Column('require_approval', sa.Boolean(), nullable=False),
    sa.Column('business_name', sa.String(length=160), nullable=True),
    sa.Column('connected_source', sa.String(length=32), nullable=True),
    sa.Column('updated_by_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('id = 1', name=op.f('ck_merchant_settings_singleton_row')),
    sa.CheckConstraint('max_daily_spend_paise IS NULL OR max_daily_spend_paise > 0', name=op.f('ck_merchant_settings_max_daily_spend_positive')),
    sa.CheckConstraint('max_order_spend_paise IS NULL OR max_order_spend_paise > 0', name=op.f('ck_merchant_settings_max_order_spend_positive')),
    sa.CheckConstraint('max_reorder_quantity IS NULL OR max_reorder_quantity > 0', name=op.f('ck_merchant_settings_max_reorder_quantity_positive')),
    sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], name=op.f('fk_merchant_settings_updated_by_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_merchant_settings'))
    )
    op.create_table('payment_methods',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('brand', sa.String(length=32), nullable=False),
    sa.Column('last4', sa.String(length=4), nullable=False),
    sa.Column('exp_month', sa.Integer(), nullable=False),
    sa.Column('exp_year', sa.Integer(), nullable=False),
    sa.Column('gateway_token', sa.String(length=128), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('exp_month BETWEEN 1 AND 12', name=op.f('ck_payment_methods_exp_month_valid')),
    sa.CheckConstraint('exp_year >= 2000', name=op.f('ck_payment_methods_exp_year_valid')),
    sa.CheckConstraint('length(last4) = 4', name=op.f('ck_payment_methods_last4_is_four_digits')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_payment_methods_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_methods'))
    )
    with op.batch_alter_table('payment_methods', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_methods_user_id'), ['user_id'], unique=False)

    op.create_table('sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions'))
    )
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sessions_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_sessions_token_hash'), ['token_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_sessions_user_id'), ['user_id'], unique=False)

    op.create_table('subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=False),
    sa.Column('billing_cycle', sa.Enum('monthly', 'annual', name='billing_cycle', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('status', sa.Enum('active', 'cancelled', name='subscription_status', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], name=op.f('fk_subscriptions_plan_id_plans'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_subscriptions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_subscriptions'))
    )
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_subscriptions_plan_id'), ['plan_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_subscriptions_user_id'), ['user_id'], unique=True)

    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.add_column(sa.Column('selling_price_paise', sa.BigInteger(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_column('selling_price_paise')

    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_subscriptions_user_id'))
        batch_op.drop_index(batch_op.f('ix_subscriptions_plan_id'))

    op.drop_table('subscriptions')
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sessions_user_id'))
        batch_op.drop_index(batch_op.f('ix_sessions_token_hash'))
        batch_op.drop_index(batch_op.f('ix_sessions_expires_at'))

    op.drop_table('sessions')
    with op.batch_alter_table('payment_methods', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_methods_user_id'))

    op.drop_table('payment_methods')
    op.drop_table('merchant_settings')
    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invoices_user_id'))
        batch_op.drop_index(batch_op.f('ix_invoices_number'))
        batch_op.drop_index(batch_op.f('ix_invoices_issued_at'))

    op.drop_table('invoices')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    with op.batch_alter_table('plans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_plans_key'))

    op.drop_table('plans')

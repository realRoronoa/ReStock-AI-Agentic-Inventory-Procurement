"""Initial schema.

Creates the five core tables:

* products       — catalogue plus current stock and reorder threshold
* sales_history  — one row per product per day (unique), the forecast evidence
* suppliers      — per-product suppliers; authoritative price in paise
* orders         — purchase orders and their state machine
* audit_log      — append-only trail; FK to orders is SET NULL so history
                   outlives its subject

Money is stored as integer paise (see app/core/money.py). Enums are rendered as
VARCHAR + CHECK rather than native PostgreSQL enum types so the same migration
runs on SQLite and PostgreSQL. Index/constraint names come from the metadata
naming convention in app/core/database.py.

Revision ID: 245c6f606bde
Revises: 
Create Date: 2026-08-27 14:26:23.207104

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '245c6f606bde'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('products',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('current_stock', sa.Integer(), nullable=False),
    sa.Column('unit', sa.String(length=20), nullable=False),
    sa.Column('reorder_threshold', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('current_stock >= 0', name=op.f('ck_products_current_stock_non_negative')),
    sa.CheckConstraint('reorder_threshold >= 0', name=op.f('ck_products_reorder_threshold_non_negative')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_products'))
    )
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_products_name'), ['name'], unique=True)

    op.create_table('sales_history',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('quantity_sold', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('quantity_sold >= 0', name=op.f('ck_sales_history_quantity_sold_non_negative')),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_sales_history_product_id_products'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales_history')),
    sa.UniqueConstraint('product_id', 'date', name='product_id_date')
    )
    with op.batch_alter_table('sales_history', schema=None) as batch_op:
        batch_op.create_index('ix_sales_history_product_id_date', ['product_id', 'date'], unique=False)

    op.create_table('suppliers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('price_per_unit_paise', sa.BigInteger(), nullable=False),
    sa.Column('delivery_days', sa.Integer(), nullable=False),
    sa.Column('razorpay_fund_account_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('delivery_days >= 0', name=op.f('ck_suppliers_delivery_days_non_negative')),
    sa.CheckConstraint('price_per_unit_paise > 0', name=op.f('ck_suppliers_price_per_unit_positive')),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_suppliers_product_id_products'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_suppliers')),
    sa.UniqueConstraint('product_id', 'name', name='product_id_name')
    )
    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_suppliers_product_id'), ['product_id'], unique=False)

    op.create_table('orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('supplier_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('amount_paise', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('proposed', 'approved', 'paid', 'failed', 'reversed', name='order_status', native_enum=False, length=20), nullable=False),
    sa.Column('razorpay_payout_id', sa.String(length=64), nullable=True),
    sa.Column('razorpay_order_id', sa.String(length=64), nullable=True),
    sa.Column('razorpay_payment_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('amount_paise > 0', name=op.f('ck_orders_amount_positive')),
    sa.CheckConstraint('quantity > 0', name=op.f('ck_orders_quantity_positive')),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_orders_product_id_products'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], name=op.f('fk_orders_supplier_id_suppliers'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    sa.UniqueConstraint('razorpay_payout_id', name=op.f('uq_orders_razorpay_payout_id'))
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orders_product_id'), ['product_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_supplier_id'), ['supplier_id'], unique=False)

    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor', sa.Enum('agent', 'human', 'system', name='audit_actor', native_enum=False, length=16), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('reasoning_text', sa.Text(), nullable=True),
    sa.Column('related_order_id', sa.Integer(), nullable=True),
    sa.Column('metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['related_order_id'], ['orders.id'], name=op.f('fk_audit_log_related_order_id_orders'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_log_action'), ['action'], unique=False)
        batch_op.create_index('ix_audit_log_action_timestamp', ['action', 'timestamp'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_related_order_id'), ['related_order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_timestamp'), ['timestamp'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_log_timestamp'))
        batch_op.drop_index(batch_op.f('ix_audit_log_related_order_id'))
        batch_op.drop_index('ix_audit_log_action_timestamp')
        batch_op.drop_index(batch_op.f('ix_audit_log_action'))

    op.drop_table('audit_log')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_orders_supplier_id'))
        batch_op.drop_index(batch_op.f('ix_orders_status'))
        batch_op.drop_index(batch_op.f('ix_orders_product_id'))

    op.drop_table('orders')
    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_suppliers_product_id'))

    op.drop_table('suppliers')
    with op.batch_alter_table('sales_history', schema=None) as batch_op:
        batch_op.drop_index('ix_sales_history_product_id_date')

    op.drop_table('sales_history')
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_products_name'))

    op.drop_table('products')

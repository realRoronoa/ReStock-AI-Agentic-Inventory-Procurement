"""Enum CHECK constraints, and the `rejected` order status.

Two related changes, both hand-written because **Alembic autogenerate does not
compare CHECK constraints** — it cannot reflect them portably, so neither
`alembic check` nor the `compare_metadata` drift test would ever have flagged
this. `tests/test_migrations.py` gains a test that exercises the constraints
against a migrated database, which closes that gap.

1. The enum-backed columns were plain `VARCHAR` with no database validation.
   `sqlalchemy.Enum(native_enum=False)` defaults to `create_constraint=False`,
   so `orders.status`, `audit_log.actor` and `webhook_events.outcome` accepted
   any string — validation existed only in Python, which does nothing about a
   raw SQL write, a bad migration, or another service touching the database.
   The models now pass `create_constraint=True`; this migration adds the
   matching constraints.

2. `orders.status` gains a sixth value, `rejected`: a human looked at the
   proposal and declined it. Deliberately distinct from `failed`, so that "the
   merchant said no" is never confused with "the bank refused the transfer" in
   reporting or in the audit trail. Terminal, and reachable only from
   `proposed`.

`batch_alter_table` is used unconditionally: SQLite cannot add a CHECK in place
and needs the table rebuilt, while on PostgreSQL batch mode emits a plain
`ALTER TABLE ... ADD CONSTRAINT`.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "dfe80c1e476d"
down_revision: Union[str, None] = "5bc53891e7eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ORDER_STATUSES = ("proposed", "approved", "paid", "failed", "reversed", "rejected")
AUDIT_ACTORS = ("agent", "human", "system")
WEBHOOK_OUTCOMES = (
    "applied",
    "already_applied",
    "acknowledged",
    "unknown_payout",
    "ignored",
    "conflict",
)


def _in_clause(column: str, values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    # Constraint names follow the metadata naming convention
    # (ck_%(table_name)s_%(constraint_name)s), so a future migration can drop
    # them by name rather than hunting for an anonymous constraint.
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "order_status", _in_clause("status", ORDER_STATUSES)
        )

    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "audit_actor", _in_clause("actor", AUDIT_ACTORS)
        )

    with op.batch_alter_table("webhook_events", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "webhook_outcome", _in_clause("outcome", WEBHOOK_OUTCOMES)
        )


def downgrade() -> None:
    # Any order already in the `rejected` state would violate the pre-change
    # schema. Fail loudly rather than silently rewriting a human decision into
    # some other status.
    connection = op.get_bind()
    rejected = connection.execute(
        sa.text("SELECT COUNT(*) FROM orders WHERE status = 'rejected'")
    ).scalar()
    if rejected:
        raise RuntimeError(
            f"Cannot downgrade: {rejected} order(s) are in the 'rejected' state, "
            "which does not exist before this revision. Resolve those orders "
            "first — they represent explicit human decisions and must not be "
            "silently reassigned."
        )

    with op.batch_alter_table("webhook_events", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_webhook_events_webhook_outcome"), type_="check"
        )

    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_audit_log_audit_actor"), type_="check")

    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_orders_order_status"), type_="check")

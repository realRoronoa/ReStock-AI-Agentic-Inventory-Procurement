"""Human approval and payout initiation.

This is the security boundary of the whole system: the only place a proposal
turns into money leaving a bank account. Everything in it is deterministic and
no LLM code can reach it.

There is deliberately **no** auto-approve, agent-approve, approve-if-small, or
background auto-pay path. A one-rupee order needs the same explicit human call
as a ten-thousand-rupee one.

Concurrency design
------------------
The dangerous scenario is two approvals in flight at once — a double-click, two
tabs, or a client retrying after a timeout — producing two payouts. Disabling a
button does not solve this; the backend must.

Two compare-and-swap claims, each a conditional `UPDATE ... WHERE`, make it
safe. `rowcount` is the arbiter, so the database decides the winner rather than
application logic reading and then writing:

1. **Approval claim** — `SET status='approved' WHERE id=? AND status='proposed'`.
   Exactly one concurrent request can match. Losers see `rowcount == 0`.
2. **Payout claim** — `SET payout_attempted_at=now() WHERE id=? AND
   payout_attempted_at IS NULL`. Won separately, so even a resumed approval
   cannot double-send.

Both work identically on SQLite and PostgreSQL, needing no `SELECT ... FOR
UPDATE` (which SQLite does not support).

The idempotency key is generated and **committed before** the RazorpayX call, so
any retry of the same logical payout carries the same key and RazorpayX collapses
it onto the original payout instead of creating a second one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import Settings, settings as default_settings
from app.core.database import utcnow
from app.core.errors import (
    AppError,
    OrderNotProposedError,
    OrderNotRejectableError,
    PayoutAlreadyRequestedError,
    PayoutOutcomeUnknownError,
)
from app.core.limits import enforce_all, limits_snapshot
from app.core.money import format_inr
from app.models.audit_log import AuditAction, AuditActor
from app.models.order import Order, OrderStatus
from app.schemas.payment import PayoutOutcome, PayoutState
from app.services import audit_service, order_service, payment_service, supplier_service
from app.services.payment_service import PaymentProvider

logger = logging.getLogger("restock.approval")


@dataclass
class ApprovalResult:
    """Outcome of an approval request."""

    order: Order
    payout: PayoutOutcome | None
    revalidated_amount_paise: int
    price_changed_since_proposal: bool


def _revalidate(
    db: Session,
    order: Order,
    *,
    config: Settings,
) -> tuple[int, bool]:
    """Re-check every precondition and recompute the amount from live data.

    A proposal can be minutes or days old. In between, a supplier can be
    deleted, repointed at another product, have its bank details removed, or
    change its price; and other orders may have consumed the day's budget.
    Trusting the stored amount would mean paying yesterday's price with today's
    authorisation.

    Returns:
        `(authoritative_amount_paise, price_changed_since_proposal)`.
    """
    # Supplier must still exist, still belong to this product, and still be
    # payable. Re-read, not reused from the order's loaded relationship.
    supplier = supplier_service.validate_supplier_for_product(
        db, order.supplier_id, order.product, require_fund_account=True
    )

    amount_paise = supplier_service.calculate_amount_paise(supplier, order.quantity)
    price_changed = amount_paise != order.amount_paise

    if price_changed:
        # The recomputed figure wins. A merchant approved "order N from this
        # supplier"; the price of that order is whatever the database says now.
        logger.warning(
            "amount_recomputed order_id=%s stored_paise=%d live_paise=%d",
            order.id,
            order.amount_paise,
            amount_paise,
        )
        order.amount_paise = amount_paise

    # Guardrails again, against the recomputed amount. `exclude_order_id` keeps
    # this order from counting against its own daily budget on a resumed
    # approval, when it may already be APPROVED.
    enforce_all(
        db,
        quantity=order.quantity,
        amount_paise=amount_paise,
        exclude_order_id=order.id,
        config=config,
    )

    return amount_paise, price_changed


def _claim_approval(db: Session, order_id: int, idempotency_key: str) -> bool:
    """Atomically move PROPOSED -> APPROVED. True if this caller won.

    The conditional UPDATE is the entire concurrency control. A read-then-write
    would let two requests both observe PROPOSED and both proceed.
    """
    result = db.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == OrderStatus.PROPOSED)
        .values(
            status=OrderStatus.APPROVED,
            approved_at=utcnow(),
            payout_idempotency_key=idempotency_key,
            updated_at=utcnow(),
        )
    )
    return result.rowcount == 1


def _claim_payout_attempt(db: Session, order_id: int) -> bool:
    """Atomically claim the right to call RazorpayX. True if this caller won.

    Separate from the approval claim so that a resumed approval — an order that
    is APPROVED but whose payout never went out — still cannot race another
    resume attempt.
    """
    result = db.execute(
        update(Order)
        .where(
            Order.id == order_id,
            Order.payout_attempted_at.is_(None),
            Order.status == OrderStatus.APPROVED,
        )
        .values(payout_attempted_at=utcnow(), updated_at=utcnow())
    )
    return result.rowcount == 1


def approve_order(
    db: Session,
    order_id: int,
    *,
    payment_provider: PaymentProvider,
    config: Settings | None = None,
) -> ApprovalResult:
    """Approve an order and initiate its payout.

    The order ends APPROVED with a payout id stored. It does **not** become
    PAID here: only a signature-verified `payout.processed` webhook does that.
    """
    config = config or default_settings
    order = order_service.get_order(db, order_id)

    # --- state gate -------------------------------------------------------
    if order.status is not OrderStatus.PROPOSED:
        # An order that is APPROVED but never had a payout sent is resumable:
        # approval already happened, the payout did not. Everything else is a
        # conflict.
        if order.status is OrderStatus.APPROVED and not order.payout_initiated:
            logger.info("approval_resume order_id=%s", order.id)
        else:
            _audit_approval_rejected(db, order)
            if order.payout_outcome_unknown:
                raise PayoutOutcomeUnknownError(
                    f"Order {order.id} already had a payout request sent, but its "
                    "outcome is unknown. It will not be retried automatically. "
                    "Check the RazorpayX dashboard for reference "
                    f"'{payment_service.build_reference_id(order.id)}' before "
                    "taking any action.",
                    details={
                        "order_id": order.id,
                        "status": order.status.value,
                        "reference_id": payment_service.build_reference_id(order.id),
                    },
                )
            if order.status is OrderStatus.APPROVED:
                raise PayoutAlreadyRequestedError(
                    f"Order {order.id} was already approved and a payout "
                    f"({order.razorpay_payout_id}) has been requested. It will "
                    "not be requested again.",
                    details={
                        "order_id": order.id,
                        "payout_id": order.razorpay_payout_id,
                    },
                )
            raise OrderNotProposedError(
                f"Order {order.id} is {order.status.value}. Only proposed orders "
                "can be approved.",
                details={"order_id": order.id, "status": order.status.value},
            )

    # --- revalidate everything -------------------------------------------
    try:
        amount_paise, price_changed = _revalidate(db, order, config=config)
    except AppError as exc:
        _audit_approval_rejected(db, order, reason=exc.message, code=exc.code)
        raise

    # --- claim the approval ----------------------------------------------
    resuming = order.status is OrderStatus.APPROVED
    idempotency_key = (
        order.payout_idempotency_key or payment_service.new_idempotency_key()
    )

    if not resuming:
        if not _claim_approval(db, order.id, idempotency_key):
            # Another request won the race between our read and our update.
            db.rollback()
            fresh = order_service.get_order(db, order_id)
            logger.warning(
                "approval_race_lost order_id=%s now=%s", order_id, fresh.status.value
            )
            raise PayoutAlreadyRequestedError(
                f"Order {order_id} was approved by another request. It will not "
                "be approved or paid twice.",
                details={"order_id": order_id, "status": fresh.status.value},
            )

        # Persist the recomputed amount alongside the claim.
        db.execute(
            update(Order).where(Order.id == order.id).values(amount_paise=amount_paise)
        )

        audit_service.log(
            db,
            actor=AuditActor.HUMAN,
            action=AuditAction.ORDER_APPROVED,
            reasoning_text=(
                f"A human approved order {order.id}: {order.quantity} "
                f"{order.product.unit} of {order.product.name} from "
                f"{order.supplier.name} for {format_inr(amount_paise)}. "
                "Re-validated against live supplier and spend limits at approval "
                "time."
                + (
                    " NOTE: the supplier price changed since the proposal; the "
                    "amount was recomputed from the current price."
                    if price_changed
                    else ""
                )
            ),
            related_order_id=order.id,
            metadata={
                "order_id": order.id,
                "product_id": order.product_id,
                "supplier_id": order.supplier_id,
                "quantity": order.quantity,
                "amount_paise": amount_paise,
                "amount_display": format_inr(amount_paise),
                "price_changed_since_proposal": price_changed,
                "amount_at_proposal_paise": (
                    order.unit_price_paise_at_proposal * order.quantity
                    if order.unit_price_paise_at_proposal
                    else None
                ),
                "limits_at_approval": limits_snapshot(config),
            },
        )
        # Commit the authorisation before contacting RazorpayX. If the process
        # dies during the call, the approval and its idempotency key survive, so
        # the payout can be resumed with the same key instead of duplicated.
        db.commit()
        db.refresh(order)

    # --- claim the payout attempt ----------------------------------------
    if not _claim_payout_attempt(db, order.id):
        db.rollback()
        fresh = order_service.get_order(db, order_id)
        logger.warning("payout_claim_lost order_id=%s", order_id)
        if fresh.payout_outcome_unknown:
            raise PayoutOutcomeUnknownError(details={"order_id": order_id})
        raise PayoutAlreadyRequestedError(
            f"A payout has already been requested for order {order_id} "
            f"({fresh.razorpay_payout_id}). It will not be requested again.",
            details={"order_id": order_id, "payout_id": fresh.razorpay_payout_id},
        )

    audit_service.log(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.RAZORPAY_PAYOUT_REQUESTED,
        reasoning_text=(
            f"Sending a RazorpayX payout request for order {order.id}: "
            f"{format_inr(amount_paise)} to {order.supplier.name}. Idempotency "
            "key persisted before the call."
        ),
        related_order_id=order.id,
        metadata={
            "order_id": order.id,
            "amount_paise": amount_paise,
            "fund_account_id": order.supplier.razorpay_fund_account_id,
            "idempotency_key": idempotency_key,
            "provider": getattr(
                payment_provider, "name", type(payment_provider).__name__
            ),
        },
    )
    db.commit()
    db.refresh(order)

    # --- the actual payout call ------------------------------------------
    instruction = payment_service.build_instruction(
        order,
        fund_account_id=order.supplier.razorpay_fund_account_id,
        idempotency_key=idempotency_key,
        config=config,
    )

    try:
        outcome = payment_provider.create_payout(instruction)
    except AppError as exc:
        _audit_payout_failure(db, order, exc)
        raise

    # --- store the result -------------------------------------------------
    order.razorpay_payout_id = outcome.payout_id
    order.payout_status = outcome.raw_status

    audit_service.log(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.RAZORPAY_PAYOUT_CREATED,
        reasoning_text=(
            f"RazorpayX accepted the payout for order {order.id}: payout "
            f"{outcome.payout_id}, status '{outcome.raw_status}'. This means the "
            "request was accepted, NOT that the supplier has been paid. The "
            f"order stays {OrderStatus.APPROVED.value} until a verified "
            "payout.processed webhook arrives."
        ),
        related_order_id=order.id,
        metadata={
            "order_id": order.id,
            "payout_id": outcome.payout_id,
            "razorpay_status": outcome.raw_status,
            "normalised_state": outcome.state.value,
            "amount_paise": amount_paise,
            "provider": outcome.provider,
            "provider_metadata": outcome.provider_metadata,
        },
    )
    db.commit()
    db.refresh(order)

    logger.info(
        "payout_stored order_id=%s payout_id=%s status=%s order_status=%s",
        order.id,
        outcome.payout_id,
        outcome.raw_status,
        order.status.value,
    )

    if outcome.state is PayoutState.PROCESSED:
        # Even if the creation response already says "processed", the order is
        # NOT marked paid here. Settlement is only ever accepted from a
        # signature-verified webhook, so that one code path owns the transition
        # and the inventory increment.
        logger.info(
            "payout_already_processed_on_create order_id=%s — awaiting webhook "
            "before settling",
            order.id,
        )

    return ApprovalResult(
        order=order,
        payout=outcome,
        revalidated_amount_paise=amount_paise,
        price_changed_since_proposal=price_changed,
    )


def _audit_approval_rejected(
    db: Session,
    order: Order,
    *,
    reason: str | None = None,
    code: str | None = None,
) -> None:
    detail = reason or (
        f"Order is {order.status.value}; only proposed orders can be approved."
    )
    logger.info(
        "approval_rejected order_id=%s status=%s code=%s",
        order.id,
        order.status.value,
        code,
    )
    audit_service.log_independently(
        db,
        actor=AuditActor.SYSTEM,
        action=AuditAction.ORDER_APPROVAL_REJECTED,
        reasoning_text=f"Approval of order {order.id} refused: {detail}",
        related_order_id=order.id,
        metadata={
            "order_id": order.id,
            "status": order.status.value,
            "reason": detail,
            "error_code": code,
            "payout_id": order.razorpay_payout_id,
        },
    )


def _audit_payout_failure(db: Session, order: Order, exc: AppError) -> None:
    """Record a payout that could not be created.

    The order stays APPROVED with `payout_attempted_at` set. It is deliberately
    NOT marked FAILED: this system does not get to decide that a payout failed —
    only RazorpayX does, via a webhook. In particular a timeout leaves the
    outcome genuinely unknown, and marking it failed could hide a payout that
    actually went through.
    """
    unknown = exc.code == "PAYMENT_TIMEOUT"
    action = (
        AuditAction.RAZORPAY_PAYOUT_OUTCOME_UNKNOWN
        if unknown
        else AuditAction.RAZORPAY_PAYOUT_FAILED
    )

    logger.error(
        "payout_not_created order_id=%s code=%s outcome_unknown=%s",
        order.id,
        exc.code,
        unknown,
    )
    audit_service.log_independently(
        db,
        actor=AuditActor.SYSTEM,
        action=action,
        reasoning_text=(
            f"Payout request for order {order.id} did not produce a payout id: "
            f"{exc.message}"
            + (
                " The outcome is UNKNOWN — the payout may or may not exist at "
                "RazorpayX. It will not be retried automatically. The stored "
                "idempotency key makes a later deliberate retry safe."
                if unknown
                else " No payout was created."
            )
        ),
        related_order_id=order.id,
        metadata={
            "order_id": order.id,
            "error_code": exc.code,
            "error_details": exc.details,
            "outcome_unknown": unknown,
            "reference_id": payment_service.build_reference_id(order.id),
        },
    )


def reject_order(
    db: Session,
    order_id: int,
    *,
    reason: str | None = None,
) -> Order:
    """Decline a proposed order. A human decision, and terminal.

    The counterpart to approval. Nothing is sent to RazorpayX, no money moves,
    and the order can never be revived — a merchant who changes their mind
    creates a fresh proposal, which gets its own id, approval and audit trail.

    Only `PROPOSED` orders can be rejected, enforced by the same
    compare-and-swap pattern as approval so that a rejection cannot race an
    approval and leave the order in both states.
    """
    order = order_service.get_order(db, order_id)

    if order.status is not OrderStatus.PROPOSED:
        logger.info(
            "rejection_refused order_id=%s status=%s", order.id, order.status.value
        )
        audit_service.log_independently(
            db,
            actor=AuditActor.SYSTEM,
            action=AuditAction.ORDER_APPROVAL_REJECTED,
            reasoning_text=(
                f"Rejection of order {order.id} refused: it is "
                f"{order.status.value}, and only proposed orders can be "
                "rejected."
            ),
            related_order_id=order.id,
            metadata={"order_id": order.id, "status": order.status.value},
        )
        raise OrderNotRejectableError(
            f"Order {order.id} is {order.status.value}. Only proposed orders "
            "can be rejected.",
            details={"order_id": order.id, "status": order.status.value},
        )

    # Compare-and-swap, so a concurrent approve/reject cannot both win.
    result = db.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == OrderStatus.PROPOSED)
        .values(status=OrderStatus.REJECTED, updated_at=utcnow())
    )
    if result.rowcount != 1:
        db.rollback()
        fresh = order_service.get_order(db, order_id)
        logger.warning(
            "rejection_race_lost order_id=%s now=%s", order_id, fresh.status.value
        )
        raise OrderNotRejectableError(
            f"Order {order_id} was already {fresh.status.value} by another "
            "request.",
            details={"order_id": order_id, "status": fresh.status.value},
        )

    audit_service.log(
        db,
        actor=AuditActor.HUMAN,
        action=AuditAction.ORDER_REJECTED_BY_HUMAN,
        reasoning_text=(
            f"A human declined order {order.id}: {order.quantity} "
            f"{order.product.unit} of {order.product.name} from "
            f"{order.supplier.name} for {format_inr(order.amount_paise)}. "
            "No payout was requested and no money moved."
            + (f" Stated reason: {reason}" if reason else "")
        ),
        related_order_id=order.id,
        metadata={
            "order_id": order.id,
            "product_id": order.product_id,
            "supplier_id": order.supplier_id,
            "amount_paise": order.amount_paise,
            "reason": reason,
        },
    )
    db.commit()
    db.refresh(order)

    logger.info("order_rejected order_id=%s", order.id)
    return order

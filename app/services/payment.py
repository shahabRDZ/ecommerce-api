"""
Payment service — thin abstraction over Stripe.

All monetary amounts are handled in cents (int) at the Stripe boundary;
the rest of the application uses Decimal.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID

import stripe

from app.config import settings

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


# ── Value objects ─────────────────────────────────────────────────────────────


class PaymentIntent:
    def __init__(
        self,
        intent_id: str,
        client_secret: str,
        amount: int,
        currency: str,
        status: str,
    ) -> None:
        self.intent_id = intent_id
        self.client_secret = client_secret
        self.amount = amount
        self.currency = currency
        self.status = status


class RefundResult:
    def __init__(self, refund_id: str, amount: int, status: str) -> None:
        self.refund_id = refund_id
        self.amount = amount
        self.status = status


# ── Helpers ───────────────────────────────────────────────────────────────────


def _to_cents(amount: Decimal) -> int:
    """Convert a Decimal dollar amount to integer cents."""
    return int((amount * 100).quantize(Decimal("1")))


# ── Service ───────────────────────────────────────────────────────────────────


class PaymentService:
    """
    Wraps Stripe's PaymentIntent flow.

    Usage:
        svc = PaymentService()
        intent = await svc.create_payment_intent(total, order_id, customer_id)
        # frontend confirms with intent.client_secret
        confirmed = await svc.confirm_payment(intent.intent_id, payment_method_id)
    """

    async def create_payment_intent(
        self,
        amount: Decimal,
        order_id: UUID,
        stripe_customer_id: Optional[str] = None,
        currency: str = "usd",
        metadata: Optional[dict] = None,
    ) -> PaymentIntent:
        """
        Create a Stripe PaymentIntent and return structured result.
        Raises stripe.StripeError on failure.
        """
        params: dict = {
            "amount": _to_cents(amount),
            "currency": currency,
            "metadata": {
                "order_id": str(order_id),
                **(metadata or {}),
            },
            "automatic_payment_methods": {"enabled": True},
        }
        if stripe_customer_id:
            params["customer"] = stripe_customer_id

        try:
            intent = stripe.PaymentIntent.create(**params)
            logger.info(
                "PaymentIntent created",
                extra={"intent_id": intent.id, "order_id": str(order_id)},
            )
            return PaymentIntent(
                intent_id=intent.id,
                client_secret=intent.client_secret,
                amount=intent.amount,
                currency=intent.currency,
                status=intent.status,
            )
        except stripe.StripeError as exc:
            logger.error("Stripe PaymentIntent creation failed: %s", exc)
            raise

    async def confirm_payment(
        self,
        payment_intent_id: str,
        payment_method_id: str,
    ) -> PaymentIntent:
        """Confirm a PaymentIntent server-side (for server-driven flows)."""
        try:
            intent = stripe.PaymentIntent.confirm(
                payment_intent_id,
                payment_method=payment_method_id,
            )
            logger.info(
                "PaymentIntent confirmed",
                extra={"intent_id": intent.id, "status": intent.status},
            )
            return PaymentIntent(
                intent_id=intent.id,
                client_secret=intent.client_secret,
                amount=intent.amount,
                currency=intent.currency,
                status=intent.status,
            )
        except stripe.StripeError as exc:
            logger.error("Stripe confirm failed: %s", exc)
            raise

    async def retrieve_payment_intent(self, payment_intent_id: str) -> PaymentIntent:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        return PaymentIntent(
            intent_id=intent.id,
            client_secret=intent.client_secret,
            amount=intent.amount,
            currency=intent.currency,
            status=intent.status,
        )

    async def cancel_payment_intent(self, payment_intent_id: str) -> bool:
        """Cancel a PaymentIntent that hasn't been captured yet."""
        try:
            intent = stripe.PaymentIntent.cancel(payment_intent_id)
            logger.info("PaymentIntent cancelled: %s", payment_intent_id)
            return intent.status == "canceled"
        except stripe.StripeError as exc:
            logger.error("Stripe cancel failed: %s", exc)
            return False

    async def refund(
        self,
        payment_intent_id: str,
        amount: Optional[Decimal] = None,
        reason: str = "requested_by_customer",
    ) -> RefundResult:
        """
        Issue a full or partial refund.
        If amount is None, a full refund is issued.
        """
        params: dict = {
            "payment_intent": payment_intent_id,
            "reason": reason,
        }
        if amount is not None:
            params["amount"] = _to_cents(amount)

        try:
            refund = stripe.Refund.create(**params)
            logger.info(
                "Refund issued",
                extra={"refund_id": refund.id, "payment_intent_id": payment_intent_id},
            )
            return RefundResult(
                refund_id=refund.id,
                amount=refund.amount,
                status=refund.status,
            )
        except stripe.StripeError as exc:
            logger.error("Stripe refund failed: %s", exc)
            raise

    async def create_or_get_customer(
        self, email: str, name: Optional[str] = None
    ) -> str:
        """Return existing Stripe customer ID or create a new one."""
        existing = stripe.Customer.search(query=f'email:"{email}"', limit=1)
        if existing.data:
            return existing.data[0].id

        customer = stripe.Customer.create(email=email, name=name or "")
        logger.info("Stripe customer created: %s", customer.id)
        return customer.id

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> stripe.Event:
        """Verify and parse an incoming Stripe webhook."""
        return stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )


# Singleton
payment_service = PaymentService()

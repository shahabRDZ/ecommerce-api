"""
Order management endpoints.

POST /orders                    — place a new order from current cart
GET  /orders                    — current user's order history (paginated)
GET  /orders/{id}               — single order detail
POST /orders/{id}/cancel        — customer-initiated cancellation
POST /webhooks/stripe           — Stripe webhook handler
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.cart import Cart, CartItem
from app.models.order import Order, OrderItem
from app.schemas.order import (
    OrderResponse,
    PaginatedOrderResponse,
    PaymentIntentResponse,
    PlaceOrderRequest,
)
from app.services.inventory import inventory_service
from app.services.payment import payment_service

router = APIRouter(prefix="/orders", tags=["Orders"])
webhook_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# ── Constants ─────────────────────────────────────────────────────────────────
_TAX_RATE = Decimal("0.08")  # 8% flat — replace with geo-based lookup in prod
_FREE_SHIPPING_THRESHOLD = Decimal("50.00")
_FLAT_SHIPPING_COST = Decimal("5.99")


def _calculate_shipping(subtotal: Decimal) -> Decimal:
    return (
        Decimal("0.00") if subtotal >= _FREE_SHIPPING_THRESHOLD else _FLAT_SHIPPING_COST
    )


def _generate_order_number() -> str:
    import random
    import string

    return "ORD-" + "".join(
        random.choices(string.ascii_uppercase + string.digits, k=10)
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_order_or_404(
    order_id: uuid.UUID,
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
) -> Order:
    q = select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    if user_id:
        q = q.where(Order.user_id == user_id)

    result = await db.execute(q)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    return order


# ── Place order ───────────────────────────────────────────────────────────────


@router.post(
    "", response_model=PaymentIntentResponse, status_code=status.HTTP_201_CREATED
)
async def place_order(
    payload: PlaceOrderRequest,
    db: AsyncSession = Depends(get_db),
) -> PaymentIntentResponse:
    """
    Convert the current user's cart into a confirmed Order and create a
    Stripe PaymentIntent.  The client must confirm the intent using
    ``intent.client_secret`` before the order becomes PAID.

    Flow:
    1.  Load cart + validate items are still available.
    2.  Deduct stock (within the same transaction).
    3.  Create Order + OrderItems.
    4.  Clear cart.
    5.  Create Stripe PaymentIntent.
    6.  Commit.  Return client_secret to frontend.
    """
    # TODO: replace with real auth dependency
    # For demo: resolve a mock user_id from header
    mock_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    # ── Load cart ──────────────────────────────────────────────────────────────
    cart_result = await db.execute(
        select(Cart)
        .where(Cart.user_id == mock_user_id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    cart: Cart | None = cart_result.scalar_one_or_none()

    if not cart or not cart.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cart is empty",
        )

    # ── Validate all items ────────────────────────────────────────────────────
    for cart_item in cart.items:
        if not cart_item.product.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product '{cart_item.product.name}' is no longer available",
            )
        check = await inventory_service.check_availability(
            db, cart_item.product_id, cart_item.quantity
        )
        if not check.is_available:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{cart_item.product.name}' has insufficient stock "
                f"(requested {cart_item.quantity}, available {check.available})",
            )

    # ── Calculate totals ──────────────────────────────────────────────────────
    subtotal = cart.subtotal
    shipping = _calculate_shipping(subtotal)
    tax = (subtotal * _TAX_RATE).quantize(Decimal("0.01"))
    discount = cart.discount_amount
    total = subtotal + shipping + tax - discount

    # ── Deduct stock ──────────────────────────────────────────────────────────
    for cart_item in cart.items:
        success = await inventory_service.deduct_stock(
            db, cart_item.product_id, cart_item.quantity
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Stock deduction failed for '{cart_item.product.name}'",
            )

    # ── Build Order ───────────────────────────────────────────────────────────
    addr = payload.shipping_address
    order = Order(
        order_number=_generate_order_number(),
        user_id=mock_user_id,
        status="pending",
        payment_status="pending",
        subtotal=subtotal,
        shipping_cost=shipping,
        tax_amount=tax,
        discount_amount=discount,
        total=total,
        payment_method="stripe",
        coupon_code=cart.coupon_code,
        notes=payload.notes,
        shipping_name=addr.name,
        shipping_address_line1=addr.address_line1,
        shipping_address_line2=addr.address_line2,
        shipping_city=addr.city,
        shipping_state=addr.state,
        shipping_postal_code=addr.postal_code,
        shipping_country=addr.country,
    )
    db.add(order)
    await db.flush()  # get order.id

    for cart_item in cart.items:
        order_item = OrderItem(
            order_id=order.id,
            product_id=cart_item.product_id,
            product_name=cart_item.product.name,
            product_sku=cart_item.product.sku,
            unit_price=cart_item.unit_price,
            quantity=cart_item.quantity,
        )
        db.add(order_item)

    # ── Clear cart ────────────────────────────────────────────────────────────
    for item in list(cart.items):
        await db.delete(item)
    cart.coupon_code = None
    cart.discount_amount = Decimal("0.00")

    await db.flush()

    # ── Create Stripe PaymentIntent ───────────────────────────────────────────
    intent = await payment_service.create_payment_intent(
        amount=total,
        order_id=order.id,
        metadata={"order_number": order.order_number},
    )
    order.payment_intent_id = intent.intent_id

    # Commit happens when the session dependency exits
    return PaymentIntentResponse(
        client_secret=intent.client_secret,
        amount=intent.amount,
        currency=intent.currency,
        order_id=order.id,
        order_number=order.order_number,
    )


# ── Order history ─────────────────────────────────────────────────────────────


@router.get("", response_model=PaginatedOrderResponse)
async def list_orders(
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
) -> PaginatedOrderResponse:
    mock_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    page_size = min(page_size, 100)

    total: int = (
        await db.execute(
            select(func.count()).select_from(Order).where(Order.user_id == mock_user_id)
        )
    ).scalar_one()

    orders = (
        (
            await db.execute(
                select(Order)
                .where(Order.user_id == mock_user_id)
                .options(selectinload(Order.items))
                .order_by(Order.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    total_pages = max(1, math.ceil(total / page_size))

    from app.schemas.order import OrderListItem

    items = [
        OrderListItem(
            id=o.id,
            order_number=o.order_number,
            status=o.status,
            payment_status=o.payment_status,
            total=o.total,
            item_count=sum(i.quantity for i in o.items),
            created_at=o.created_at,
        )
        for o in orders
    ]
    return PaginatedOrderResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1,
    )


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Order:
    mock_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return await _get_order_or_404(order_id, db, user_id=mock_user_id)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Order:
    mock_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    order = await _get_order_or_404(order_id, db, user_id=mock_user_id)

    cancellable_statuses = {"pending", "confirmed"}
    if order.status not in cancellable_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Order with status '{order.status}' cannot be cancelled",
        )

    order.status = "cancelled"
    order.cancelled_at = datetime.now(timezone.utc)

    # Restore stock
    for item in order.items:
        await inventory_service.restock(
            db, item.product_id, item.quantity, reason="order_cancelled"
        )

    # Cancel Stripe intent if present
    if order.payment_intent_id:
        await payment_service.cancel_payment_intent(order.payment_intent_id)

    await db.flush()
    return order


# ── Stripe webhook ────────────────────────────────────────────────────────────


@webhook_router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    payload = await request.body()

    try:
        event = payment_service.construct_webhook_event(payload, stripe_signature)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature"
        )

    event_type: str = event["type"]
    data_obj = event["data"]["object"]

    if event_type == "payment_intent.succeeded":
        intent_id: str = data_obj["id"]
        result = await db.execute(
            select(Order)
            .where(Order.payment_intent_id == intent_id)
            .options(selectinload(Order.items))
        )
        order: Order | None = result.scalar_one_or_none()
        if order:
            order.payment_status = "paid"
            order.status = "confirmed"
            order.confirmed_at = datetime.now(timezone.utc)
            await db.flush()

    elif event_type == "payment_intent.payment_failed":
        intent_id = data_obj["id"]
        result = await db.execute(
            select(Order).where(Order.payment_intent_id == intent_id)
        )
        order = result.scalar_one_or_none()
        if order:
            order.payment_status = "failed"
            await db.flush()

    return {"received": True}

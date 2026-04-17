"""
Integration tests — Order flow endpoints.

Tests cover: empty-cart guard, order list/detail, cancellation rules,
admin order management, and the Stripe webhook handler.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cart import Cart, CartItem
from app.models.order import Order
from app.models.product import Product


# ── Helpers ────────────────────────────────────────────────────────────────────

_MOCK_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

_SHIPPING = {
    "name": "Jane Doe",
    "address_line1": "123 Main St",
    "address_line2": "Apt 4B",
    "city": "Springfield",
    "state": "IL",
    "postal_code": "62701",
    "country": "US",
}


async def _seed_cart_with_product(db_session: AsyncSession, product: Product) -> Cart:
    """Create a cart belonging to the mock user with one item."""
    cart = Cart(user_id=_MOCK_USER_ID)
    db_session.add(cart)
    await db_session.flush()

    item = CartItem(
        cart_id=cart.id,
        product_id=product.id,
        quantity=2,
        unit_price=product.price,
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(cart)
    return cart


# ── Order list / detail ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_orders_empty(client: AsyncClient) -> None:
    r = await client.get("/api/v1/orders")
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["has_next"] is False


@pytest.mark.asyncio
async def test_list_orders_pagination_params(client: AsyncClient) -> None:
    r = await client.get("/api/v1/orders?page=1&page_size=10")
    assert r.status_code == 200
    data = r.json()
    assert data["page"] == 1
    assert data["page_size"] == 10


@pytest.mark.asyncio
async def test_get_order_not_found(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/orders/{uuid.uuid4()}")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


# ── Place order ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_place_order_empty_cart(client: AsyncClient) -> None:
    """Attempting to place an order with no cart returns 400."""
    r = await client.post(
        "/api/v1/orders",
        json={"shipping_address": _SHIPPING, "payment_method_id": "pm_test"},
    )
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_place_order_success(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_product: Product,
) -> None:
    """Happy-path: cart → order → PaymentIntent stub."""
    await _seed_cart_with_product(db_session, sample_product)

    mock_intent = MagicMock()
    mock_intent.intent_id = "pi_test_123"
    mock_intent.client_secret = "pi_test_123_secret_xyz"
    mock_intent.amount = 10998  # cents
    mock_intent.currency = "usd"
    mock_intent.status = "requires_confirmation"

    with patch(
        "app.routers.orders.payment_service.create_payment_intent",
        new_callable=AsyncMock,
        return_value=mock_intent,
    ):
        r = await client.post(
            "/api/v1/orders",
            json={"shipping_address": _SHIPPING, "payment_method_id": "pm_test_visa"},
        )

    assert r.status_code == 201
    body = r.json()
    assert body["client_secret"] == "pi_test_123_secret_xyz"
    assert body["currency"] == "usd"
    assert "order_id" in body
    assert body["order_number"].startswith("ORD-")


@pytest.mark.asyncio
async def test_place_order_clears_cart(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_product: Product,
) -> None:
    """After a successful order the cart items should be cleared."""
    await _seed_cart_with_product(db_session, sample_product)

    mock_intent = MagicMock()
    mock_intent.intent_id = "pi_clear_test"
    mock_intent.client_secret = "pi_clear_secret"
    mock_intent.amount = 5000
    mock_intent.currency = "usd"
    mock_intent.status = "requires_confirmation"

    with patch(
        "app.routers.orders.payment_service.create_payment_intent",
        new_callable=AsyncMock,
        return_value=mock_intent,
    ):
        r = await client.post(
            "/api/v1/orders",
            json={"shipping_address": _SHIPPING, "payment_method_id": "pm_test"},
        )
    assert r.status_code == 201

    # Cart should now be empty
    cart_r = await client.get("/api/v1/cart")
    assert cart_r.json()["item_count"] == 0


@pytest.mark.asyncio
async def test_place_order_out_of_stock(
    client: AsyncClient,
    db_session: AsyncSession,
    out_of_stock_product: Product,
) -> None:
    """Order placement fails when a cart item is out of stock."""
    await _seed_cart_with_product(db_session, out_of_stock_product)

    r = await client.post(
        "/api/v1/orders",
        json={"shipping_address": _SHIPPING, "payment_method_id": "pm_test"},
    )
    assert r.status_code == 422


# ── Cancel order ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_order_not_found(client: AsyncClient) -> None:
    r = await client.post(f"/api/v1/orders/{uuid.uuid4()}/cancel")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cancel_pending_order(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_product: Product,
) -> None:
    """A PENDING order can be cancelled; stock should be restored."""
    order = Order(
        order_number="ORD-TESTCANCEL",
        user_id=_MOCK_USER_ID,
        status="pending",
        payment_status="unpaid",
        subtotal=Decimal("99.98"),
        shipping_cost=Decimal("5.99"),
        tax_amount=Decimal("8.00"),
        discount_amount=Decimal("0.00"),
        total=Decimal("113.97"),
        shipping_name="Test User",
        shipping_address_line1="1 Test Lane",
        shipping_city="Testville",
        shipping_state="CA",
        shipping_postal_code="90210",
        shipping_country="US",
    )
    db_session.add(order)
    await db_session.flush()

    from app.models.order import OrderItem as OI

    oi = OI(
        order_id=order.id,
        product_id=sample_product.id,
        product_name=sample_product.name,
        product_sku=sample_product.sku,
        unit_price=sample_product.price,
        quantity=2,
    )
    db_session.add(oi)
    await db_session.commit()

    r = await client.post(f"/api/v1/orders/{order.id}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None


@pytest.mark.asyncio
async def test_cancel_delivered_order_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_product: Product,
) -> None:
    """Orders in DELIVERED status cannot be cancelled by the customer."""
    order = Order(
        order_number="ORD-DELIVERED",
        user_id=_MOCK_USER_ID,
        status="delivered",
        payment_status="paid",
        subtotal=Decimal("49.99"),
        shipping_cost=Decimal("0.00"),
        tax_amount=Decimal("4.00"),
        discount_amount=Decimal("0.00"),
        total=Decimal("53.99"),
        shipping_name="Test",
        shipping_address_line1="1 St",
        shipping_city="City",
        shipping_state="ST",
        shipping_postal_code="00000",
        shipping_country="US",
    )
    db_session.add(order)
    await db_session.commit()

    r = await client.post(f"/api/v1/orders/{order.id}/cancel")
    assert r.status_code == 422


# ── Admin order management ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_list_orders(client: AsyncClient) -> None:
    r = await client.get("/api/v1/admin/orders")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_admin_get_order_not_found(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/admin/orders/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_admin_update_order_status_to_shipped(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_product: Product,
) -> None:
    order = Order(
        order_number="ORD-SHIP-TEST",
        user_id=_MOCK_USER_ID,
        status="confirmed",
        payment_status="paid",
        subtotal=Decimal("49.99"),
        shipping_cost=Decimal("5.99"),
        tax_amount=Decimal("4.00"),
        discount_amount=Decimal("0.00"),
        total=Decimal("59.98"),
        shipping_name="Test",
        shipping_address_line1="1 St",
        shipping_city="City",
        shipping_state="ST",
        shipping_postal_code="00000",
        shipping_country="US",
    )
    db_session.add(order)
    await db_session.commit()

    r = await client.patch(
        f"/api/v1/admin/orders/{order.id}/status",
        json={
            "status": "shipped",
            "tracking_number": "1Z999AA1012345678",
            "carrier": "UPS",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "shipped"
    assert body["tracking_number"] == "1Z999AA1012345678"
    assert body["carrier"] == "UPS"
    assert body["shipped_at"] is not None


# ── Stripe webhook ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stripe_webhook_invalid_signature(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/webhooks/stripe",
        content=b'{"type": "payment_intent.succeeded"}',
        headers={"stripe-signature": "invalid_sig"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_stripe_webhook_payment_succeeded(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_product: Product,
) -> None:
    """Simulate a payment_intent.succeeded event updating order status."""
    order = Order(
        order_number="ORD-WEBHOOK-001",
        user_id=_MOCK_USER_ID,
        status="pending",
        payment_status="pending",
        payment_intent_id="pi_webhook_test",
        subtotal=Decimal("49.99"),
        shipping_cost=Decimal("5.99"),
        tax_amount=Decimal("4.00"),
        discount_amount=Decimal("0.00"),
        total=Decimal("59.98"),
        shipping_name="Hook",
        shipping_address_line1="1 Webhook St",
        shipping_city="City",
        shipping_state="ST",
        shipping_postal_code="00000",
        shipping_country="US",
    )
    db_session.add(order)
    await db_session.commit()

    fake_event = {
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_webhook_test"}},
    }

    with patch(
        "app.routers.orders.payment_service.construct_webhook_event",
        return_value=fake_event,
    ):
        r = await client.post(
            "/api/v1/webhooks/stripe",
            content=b"fake_payload",
            headers={"stripe-signature": "t=1234,v1=sig"},
        )

    assert r.status_code == 200
    assert r.json()["received"] is True

    # Reload order and verify status update
    from sqlalchemy import select
    from app.models.order import Order as OrderModel

    updated = (
        await db_session.execute(select(OrderModel).where(OrderModel.id == order.id))
    ).scalar_one()
    assert updated.payment_status == "paid"
    assert updated.status == "confirmed"

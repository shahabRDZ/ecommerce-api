from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Shared address schema ─────────────────────────────────────────────────────

class ShippingAddress(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    address_line1: str = Field(..., min_length=1, max_length=255)
    address_line2: Optional[str] = Field(None, max_length=255)
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)
    postal_code: str = Field(..., min_length=1, max_length=20)
    country: str = Field(default="US", min_length=2, max_length=100)


# ── Request bodies ────────────────────────────────────────────────────────────

class PlaceOrderRequest(BaseModel):
    shipping_address: ShippingAddress
    payment_method_id: str = Field(..., description="Stripe PaymentMethod ID")
    coupon_code: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = Field(None, max_length=1000)


class UpdateOrderStatusRequest(BaseModel):
    status: str = Field(
        ...,
        pattern=r"^(pending|confirmed|processing|shipped|delivered|cancelled|refunded)$",
    )
    tracking_number: Optional[str] = Field(None, max_length=200)
    carrier: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = Field(None, max_length=1000)


# ── Response schemas ──────────────────────────────────────────────────────────

class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    product_sku: str
    unit_price: Decimal
    quantity: int
    discount_amount: Decimal
    line_total: Decimal


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    status: str
    payment_status: str
    items: List[OrderItemResponse]

    subtotal: Decimal
    shipping_cost: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    total: Decimal

    payment_method: Optional[str]
    coupon_code: Optional[str]
    tracking_number: Optional[str]
    carrier: Optional[str]
    notes: Optional[str]

    # Shipping address snapshot
    shipping_name: Optional[str]
    shipping_address_line1: Optional[str]
    shipping_address_line2: Optional[str]
    shipping_city: Optional[str]
    shipping_state: Optional[str]
    shipping_postal_code: Optional[str]
    shipping_country: Optional[str]

    created_at: datetime
    updated_at: datetime
    confirmed_at: Optional[datetime]
    shipped_at: Optional[datetime]
    delivered_at: Optional[datetime]
    cancelled_at: Optional[datetime]


class OrderListItem(BaseModel):
    """Slim representation for the order history list."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    status: str
    payment_status: str
    total: Decimal
    item_count: int = Field(default=0)
    created_at: datetime


class PaginatedOrderResponse(BaseModel):
    items: List[OrderListItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool


# ── Payment intent ────────────────────────────────────────────────────────────

class PaymentIntentResponse(BaseModel):
    client_secret: str
    amount: int  # cents
    currency: str
    order_id: uuid.UUID
    order_number: str

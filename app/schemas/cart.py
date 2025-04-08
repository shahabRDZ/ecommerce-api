from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Request bodies ────────────────────────────────────────────────────────────

class AddToCartRequest(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=999)


class UpdateCartItemRequest(BaseModel):
    quantity: int = Field(..., ge=1, le=999)


class ApplyCouponRequest(BaseModel):
    coupon_code: str = Field(..., min_length=1, max_length=50)


# ── Response schemas ──────────────────────────────────────────────────────────

class CartItemProductSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    sku: str
    slug: str
    thumbnail_url: Optional[str]
    is_in_stock: bool
    stock_quantity: int


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    product: CartItemProductSnapshot
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    added_at: datetime


class CartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    items: List[CartItemResponse]
    item_count: int
    subtotal: Decimal
    discount_amount: Decimal
    total: Decimal
    coupon_code: Optional[str]
    updated_at: datetime


class CartSummary(BaseModel):
    """Lightweight summary for headers / mini-cart widgets."""
    item_count: int
    total: Decimal

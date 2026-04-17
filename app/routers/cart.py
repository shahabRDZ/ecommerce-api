"""
Shopping cart endpoints.

GET    /cart             — retrieve current cart
POST   /cart/items       — add item to cart
PATCH  /cart/items/{id}  — update item quantity
DELETE /cart/items/{id}  — remove item from cart
POST   /cart/coupon      — apply coupon code
DELETE /cart/coupon      — remove applied coupon
DELETE /cart             — clear entire cart
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.schemas.cart import (
    AddToCartRequest,
    ApplyCouponRequest,
    CartResponse,
    CartSummary,
    UpdateCartItemRequest,
)
from app.services.inventory import inventory_service

router = APIRouter(prefix="/cart", tags=["Cart"])

# ── Coupon logic (stub — plug in your promotion engine) ───────────────────────
_MOCK_COUPONS: dict[str, Decimal] = {
    "SAVE10": Decimal("10.00"),
    "SAVE20": Decimal("20.00"),
    "WELCOME": Decimal("5.00"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_or_create_cart(
    request: Request,
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
) -> Cart:
    """
    Retrieve an existing cart for the authenticated user, or fall back to
    a session-based guest cart.  Creates a new cart if none exists.
    """
    if user_id:
        result = await db.execute(
            select(Cart)
            .where(Cart.user_id == user_id)
            .options(selectinload(Cart.items).selectinload(CartItem.product))
        )
        cart = result.scalar_one_or_none()
        if cart:
            return cart
        cart = Cart(user_id=user_id)
    else:
        session_token = request.cookies.get("cart_session")
        if session_token:
            result = await db.execute(
                select(Cart)
                .where(Cart.session_token == session_token)
                .options(selectinload(Cart.items).selectinload(CartItem.product))
            )
            cart = result.scalar_one_or_none()
            if cart:
                return cart
        cart = Cart(session_token=str(uuid.uuid4()))

    db.add(cart)
    await db.flush()
    return cart


async def _get_cart_item_or_404(item_id: uuid.UUID, cart: Cart) -> CartItem:
    for item in cart.items:
        if item.id == item_id:
            return item
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Cart item not found",
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=CartResponse)
async def get_cart(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Cart:
    # TODO: inject current_user when auth is wired; for now use guest cart
    return await _get_or_create_cart(request, db, user_id=None)


@router.post("/items", response_model=CartResponse, status_code=status.HTTP_201_CREATED)
async def add_to_cart(
    payload: AddToCartRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Cart:
    # Verify product exists and is active
    result = await db.execute(
        select(Product).where(
            Product.id == payload.product_id,
            Product.is_active.is_(True),
        )
    )
    product: Product | None = result.scalar_one_or_none()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found or inactive",
        )

    # Check stock
    stock_check = await inventory_service.check_availability(
        db, payload.product_id, payload.quantity
    )
    if not stock_check.is_available:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient stock. Available: {stock_check.available}",
        )

    cart = await _get_or_create_cart(request, db, user_id=None)

    # If item already exists in cart, increment quantity
    existing_item: CartItem | None = next(
        (i for i in cart.items if i.product_id == payload.product_id), None
    )
    if existing_item:
        new_qty = existing_item.quantity + payload.quantity
        # Re-check total quantity
        total_check = await inventory_service.check_availability(
            db, payload.product_id, new_qty
        )
        if not total_check.is_available:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot add {payload.quantity} more — only {stock_check.available} available",
            )
        existing_item.quantity = new_qty
    else:
        item = CartItem(
            cart_id=cart.id,
            product_id=product.id,
            quantity=payload.quantity,
            unit_price=product.price,
        )
        db.add(item)

    await db.flush()
    await db.refresh(cart)
    # Reload items with products
    result = await db.execute(
        select(Cart)
        .where(Cart.id == cart.id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    return result.scalar_one()


@router.patch("/items/{item_id}", response_model=CartResponse)
async def update_cart_item(
    item_id: uuid.UUID,
    payload: UpdateCartItemRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Cart:
    cart = await _get_or_create_cart(request, db, user_id=None)
    item = await _get_cart_item_or_404(item_id, cart)

    stock_check = await inventory_service.check_availability(
        db, item.product_id, payload.quantity
    )
    if not stock_check.is_available:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Only {stock_check.available} units available",
        )

    item.quantity = payload.quantity
    await db.flush()

    result = await db.execute(
        select(Cart)
        .where(Cart.id == cart.id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    return result.scalar_one()


@router.delete("/items/{item_id}", response_model=CartResponse)
async def remove_cart_item(
    item_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Cart:
    cart = await _get_or_create_cart(request, db, user_id=None)
    item = await _get_cart_item_or_404(item_id, cart)
    await db.delete(item)
    await db.flush()

    result = await db.execute(
        select(Cart)
        .where(Cart.id == cart.id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    return result.scalar_one()


@router.post("/coupon", response_model=CartResponse)
async def apply_coupon(
    payload: ApplyCouponRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Cart:
    code = payload.coupon_code.upper()
    discount = _MOCK_COUPONS.get(code)
    if discount is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired coupon code",
        )

    cart = await _get_or_create_cart(request, db, user_id=None)
    if cart.subtotal < discount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Coupon discount exceeds cart total",
        )

    cart.coupon_code = code
    cart.discount_amount = discount
    await db.flush()

    result = await db.execute(
        select(Cart)
        .where(Cart.id == cart.id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    return result.scalar_one()


@router.delete("/coupon", response_model=CartResponse)
async def remove_coupon(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Cart:
    cart = await _get_or_create_cart(request, db, user_id=None)
    cart.coupon_code = None
    cart.discount_amount = Decimal("0.00")
    await db.flush()

    result = await db.execute(
        select(Cart)
        .where(Cart.id == cart.id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
    )
    return result.scalar_one()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def clear_cart(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    cart = await _get_or_create_cart(request, db, user_id=None)
    for item in list(cart.items):
        await db.delete(item)
    cart.coupon_code = None
    cart.discount_amount = Decimal("0.00")
    await db.flush()


@router.get("/summary", response_model=CartSummary)
async def cart_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CartSummary:
    cart = await _get_or_create_cart(request, db, user_id=None)
    return CartSummary(item_count=cart.item_count, total=cart.total)

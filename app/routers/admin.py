"""
Admin endpoints.

All routes require role == 'admin' or role == 'staff'.
Exposed under /admin prefix so they can be trivially blocked at the
reverse-proxy layer for non-admin origins.

Products
    POST   /admin/products            — create product
    PATCH  /admin/products/{id}       — update product
    DELETE /admin/products/{id}       — soft-delete product
    PATCH  /admin/products/{id}/stock — adjust stock level

Orders
    GET    /admin/orders              — all orders (filterable, paginated)
    GET    /admin/orders/{id}         — order detail
    PATCH  /admin/orders/{id}/status  — update status & tracking info

Dashboard
    GET    /admin/dashboard           — high-level metrics
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.order import Order
from app.models.product import Product
from app.schemas.order import (
    OrderResponse,
    PaginatedOrderResponse,
    UpdateOrderStatusRequest,
)
from app.schemas.product import (
    PaginatedProductResponse,
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
)
from app.services.inventory import StockAdjustment, inventory_service
from pydantic import BaseModel, Field

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Simple stock-adjustment request ──────────────────────────────────────────


class StockAdjustmentRequest(BaseModel):
    delta: int = Field(..., description="Positive to add stock, negative to remove")
    reason: str = Field(default="manual_adjustment", max_length=200)


class DashboardMetrics(BaseModel):
    total_products: int
    active_products: int
    low_stock_products: int
    out_of_stock_products: int
    total_orders: int
    pending_orders: int
    revenue_today: Decimal
    revenue_total: Decimal


# ── Product management ────────────────────────────────────────────────────────


@router.get("/products", response_model=PaginatedProductResponse)
async def admin_list_products(
    q: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    low_stock: Optional[bool] = Query(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaginatedProductResponse:
    from sqlalchemy import and_, or_

    filters = []
    if q:
        term = f"%{q.lower()}%"
        filters.append(
            or_(
                func.lower(Product.name).like(term),
                func.lower(Product.sku).like(term),
            )
        )
    if is_active is not None:
        filters.append(Product.is_active.is_(is_active))
    if low_stock is True:
        filters.append(
            Product.stock_quantity <= Product.low_stock_threshold,
            Product.stock_quantity > 0,
        )

    where_clause = and_(*filters) if filters else True

    total: int = (
        await db.execute(select(func.count()).select_from(Product).where(where_clause))
    ).scalar_one()

    products = (
        (
            await db.execute(
                select(Product)
                .where(where_clause)
                .options(selectinload(Product.category))
                .order_by(Product.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    total_pages = max(1, math.ceil(total / page_size))
    return PaginatedProductResponse(
        items=[ProductListResponse.model_validate(p) for p in products],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1,
    )


@router.post(
    "/products",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_product(
    payload: ProductCreate,
    db: AsyncSession = Depends(get_db),
) -> Product:
    from sqlalchemy import or_

    existing = (
        await db.execute(
            select(Product).where(
                or_(Product.sku == payload.sku, Product.slug == payload.slug)
            )
        )
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A product with this SKU or slug already exists",
        )

    product = Product(**payload.model_dump())
    db.add(product)
    await db.flush()
    await db.refresh(product, ["category"])
    return product


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def admin_update_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    db: AsyncSession = Depends(get_db),
) -> Product:
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.category))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)

    await db.flush()
    await db.refresh(product, ["category"])
    return product


@router.delete(
    "/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def admin_delete_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
    product.is_active = False
    await db.flush()


@router.patch("/products/{product_id}/stock", response_model=ProductResponse)
async def admin_adjust_stock(
    product_id: uuid.UUID,
    payload: StockAdjustmentRequest,
    db: AsyncSession = Depends(get_db),
) -> Product:
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.category))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )

    new_qty = product.stock_quantity + payload.delta
    if new_qty < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Adjustment would result in negative stock ({new_qty})",
        )

    await inventory_service.adjust_stock(
        db,
        [
            StockAdjustment(
                product_id=product_id, delta=payload.delta, reason=payload.reason
            )
        ],
    )
    await db.refresh(product)
    return product


# ── Order management ──────────────────────────────────────────────────────────


@router.get("/orders", response_model=PaginatedOrderResponse)
async def admin_list_orders(
    status_filter: Optional[str] = Query(None, alias="status"),
    payment_status: Optional[str] = Query(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaginatedOrderResponse:
    from sqlalchemy import and_

    filters = []
    if status_filter:
        filters.append(Order.status == status_filter)
    if payment_status:
        filters.append(Order.payment_status == payment_status)

    where_clause = and_(*filters) if filters else True

    total: int = (
        await db.execute(select(func.count()).select_from(Order).where(where_clause))
    ).scalar_one()

    orders = (
        (
            await db.execute(
                select(Order)
                .where(where_clause)
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


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def admin_get_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Order:
    result = await db.execute(
        select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    return order


@router.patch("/orders/{order_id}/status", response_model=OrderResponse)
async def admin_update_order_status(
    order_id: uuid.UUID,
    payload: UpdateOrderStatusRequest,
    db: AsyncSession = Depends(get_db),
) -> Order:
    result = await db.execute(
        select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    now = datetime.now(timezone.utc)
    old_status = order.status
    order.status = payload.status

    if payload.tracking_number:
        order.tracking_number = payload.tracking_number
    if payload.carrier:
        order.carrier = payload.carrier
    if payload.notes:
        order.notes = payload.notes

    # Set relevant timestamp fields
    if payload.status == "confirmed" and old_status != "confirmed":
        order.confirmed_at = now
    elif payload.status == "shipped" and not order.shipped_at:
        order.shipped_at = now
    elif payload.status == "delivered" and not order.delivered_at:
        order.delivered_at = now
    elif payload.status == "cancelled" and not order.cancelled_at:
        order.cancelled_at = now
        # Restore stock
        for item in order.items:
            await inventory_service.restock(
                db, item.product_id, item.quantity, reason="admin_cancel"
            )

    await db.flush()
    return order


# ── Dashboard metrics ─────────────────────────────────────────────────────────


@router.get("/dashboard", response_model=DashboardMetrics)
async def dashboard(db: AsyncSession = Depends(get_db)) -> DashboardMetrics:
    from datetime import date

    today_start = datetime.combine(date.today(), datetime.min.time()).replace(
        tzinfo=timezone.utc
    )

    total_products = (
        await db.execute(select(func.count()).select_from(Product))
    ).scalar_one()

    active_products = (
        await db.execute(
            select(func.count()).select_from(Product).where(Product.is_active.is_(True))
        )
    ).scalar_one()

    low_stock = (
        await db.execute(
            select(func.count())
            .select_from(Product)
            .where(
                Product.is_active.is_(True),
                Product.stock_quantity > 0,
                Product.stock_quantity <= Product.low_stock_threshold,
            )
        )
    ).scalar_one()

    out_of_stock = (
        await db.execute(
            select(func.count())
            .select_from(Product)
            .where(Product.is_active.is_(True), Product.stock_quantity == 0)
        )
    ).scalar_one()

    total_orders = (
        await db.execute(select(func.count()).select_from(Order))
    ).scalar_one()

    pending_orders = (
        await db.execute(
            select(func.count()).select_from(Order).where(Order.status == "pending")
        )
    ).scalar_one()

    revenue_today = (
        await db.execute(
            select(func.coalesce(func.sum(Order.total), 0)).where(
                Order.payment_status == "paid",
                Order.created_at >= today_start,
            )
        )
    ).scalar_one()

    revenue_total = (
        await db.execute(
            select(func.coalesce(func.sum(Order.total), 0)).where(
                Order.payment_status == "paid"
            )
        )
    ).scalar_one()

    return DashboardMetrics(
        total_products=total_products,
        active_products=active_products,
        low_stock_products=low_stock,
        out_of_stock_products=out_of_stock,
        total_orders=total_orders,
        pending_orders=pending_orders,
        revenue_today=Decimal(str(revenue_today)),
        revenue_total=Decimal(str(revenue_total)),
    )

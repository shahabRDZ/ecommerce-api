"""
Product catalog endpoints.

GET  /products          — paginated list with filtering, search, sorting
GET  /products/{id}     — single product detail
GET  /products/slug/{slug}
GET  /categories        — list active categories
POST /categories        — create category (admin)
"""
from __future__ import annotations

import math
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.product import Category, Product
from app.schemas.product import (
    CategoryCreate,
    CategoryResponse,
    PaginatedProductResponse,
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
)

router = APIRouter(prefix="/products", tags=["Products"])
category_router = APIRouter(prefix="/categories", tags=["Categories"])


# ── Helper ────────────────────────────────────────────────────────────────────

async def _get_product_or_404(
    product_id: uuid.UUID, db: AsyncSession
) -> Product:
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.category))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


# ── Category endpoints ────────────────────────────────────────────────────────

@category_router.get("", response_model=List[CategoryResponse])
async def list_categories(
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
) -> List[Category]:
    q = select(Category).order_by(Category.sort_order, Category.name)
    if active_only:
        q = q.where(Category.is_active.is_(True))
    result = await db.execute(q)
    return result.scalars().all()


@category_router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_category(
    payload: CategoryCreate,
    db: AsyncSession = Depends(get_db),
) -> Category:
    existing = await db.execute(
        select(Category).where(Category.slug == payload.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Category with slug '{payload.slug}' already exists",
        )
    category = Category(**payload.model_dump())
    db.add(category)
    await db.flush()
    await db.refresh(category)
    return category


# ── Product list ──────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedProductResponse)
async def list_products(
    q: Optional[str] = Query(None, description="Search by name, SKU, or description"),
    category_id: Optional[int] = Query(None),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    in_stock: Optional[bool] = Query(None),
    is_featured: Optional[bool] = Query(None),
    sort_by: str = Query(default="created_at", pattern=r"^(created_at|price|name|updated_at)$"),
    sort_order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaginatedProductResponse:
    filters = [Product.is_active.is_(True)]

    if q:
        search_term = f"%{q.lower()}%"
        filters.append(
            or_(
                func.lower(Product.name).like(search_term),
                func.lower(Product.sku).like(search_term),
                func.lower(Product.description).like(search_term),
            )
        )
    if category_id is not None:
        filters.append(Product.category_id == category_id)
    if min_price is not None:
        filters.append(Product.price >= min_price)
    if max_price is not None:
        filters.append(Product.price <= max_price)
    if in_stock is True:
        filters.append(Product.stock_quantity > 0)
    elif in_stock is False:
        filters.append(Product.stock_quantity == 0)
    if is_featured is not None:
        filters.append(Product.is_featured.is_(is_featured))

    # Count total
    count_q = select(func.count()).select_from(Product).where(and_(*filters))
    total: int = (await db.execute(count_q)).scalar_one()

    # Sort
    sort_col = getattr(Product, sort_by)
    order_expr = sort_col.desc() if sort_order == "desc" else sort_col.asc()

    # Fetch page
    offset = (page - 1) * page_size
    items_q = (
        select(Product)
        .where(and_(*filters))
        .options(selectinload(Product.category))
        .order_by(order_expr)
        .offset(offset)
        .limit(page_size)
    )
    items = (await db.execute(items_q)).scalars().all()

    total_pages = max(1, math.ceil(total / page_size))
    return PaginatedProductResponse(
        items=[ProductListResponse.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1,
    )


# ── Product detail ────────────────────────────────────────────────────────────

@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Product:
    return await _get_product_or_404(product_id, db)


@router.get("/slug/{slug}", response_model=ProductResponse)
async def get_product_by_slug(
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> Product:
    result = await db.execute(
        select(Product)
        .where(Product.slug == slug, Product.is_active.is_(True))
        .options(selectinload(Product.category))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


# ── Product mutations (used by admin router too) ──────────────────────────────

@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,  # exposed via admin router
)
async def create_product(
    payload: ProductCreate,
    db: AsyncSession = Depends(get_db),
) -> Product:
    existing = await db.execute(
        select(Product).where(
            or_(Product.sku == payload.sku, Product.slug == payload.slug)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A product with this SKU or slug already exists",
        )
    product = Product(**payload.model_dump())
    db.add(product)
    await db.flush()
    await db.refresh(product, ["category"])
    return product


@router.patch("/{product_id}", response_model=ProductResponse, include_in_schema=False)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    db: AsyncSession = Depends(get_db),
) -> Product:
    product = await _get_product_or_404(product_id, db)
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)
    await db.flush()
    await db.refresh(product, ["category"])
    return product


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def delete_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    product = await _get_product_or_404(product_id, db)
    # Soft-delete: set is_active = False
    product.is_active = False
    await db.flush()

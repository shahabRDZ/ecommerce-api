from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


# ── Category schemas ──────────────────────────────────────────────────────────

class CategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=120, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    parent_id: Optional[int] = None
    sort_order: int = Field(default=0, ge=0)


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    slug: Optional[str] = Field(None, min_length=1, max_length=120, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    parent_id: Optional[int] = None
    sort_order: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class CategoryResponse(CategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime


# ── Product schemas ───────────────────────────────────────────────────────────

class ProductBase(BaseModel):
    sku: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=300, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    short_description: Optional[str] = Field(None, max_length=500)
    price: Decimal = Field(..., gt=0, decimal_places=2)
    compare_at_price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    cost_price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    category_id: Optional[int] = None
    stock_quantity: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=10, ge=0)
    track_inventory: bool = True
    weight: Optional[Decimal] = Field(None, gt=0)
    is_active: bool = True
    is_featured: bool = False
    is_digital: bool = False
    thumbnail_url: Optional[str] = Field(None, max_length=500)
    images: List[str] = Field(default_factory=list)
    meta_title: Optional[str] = Field(None, max_length=255)
    meta_description: Optional[str] = Field(None, max_length=500)
    tags: List[str] = Field(default_factory=list)

    @field_validator("compare_at_price")
    @classmethod
    def compare_price_must_exceed_price(
        cls, v: Optional[Decimal], info
    ) -> Optional[Decimal]:
        if v is not None and "price" in info.data and v <= info.data["price"]:
            raise ValueError("compare_at_price must be greater than price")
        return v


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    slug: Optional[str] = Field(None, min_length=1, max_length=300, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    short_description: Optional[str] = Field(None, max_length=500)
    price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    compare_at_price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    cost_price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    category_id: Optional[int] = None
    stock_quantity: Optional[int] = Field(None, ge=0)
    low_stock_threshold: Optional[int] = Field(None, ge=0)
    track_inventory: Optional[bool] = None
    weight: Optional[Decimal] = Field(None, gt=0)
    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None
    is_digital: Optional[bool] = None
    thumbnail_url: Optional[str] = Field(None, max_length=500)
    images: Optional[List[str]] = None
    meta_title: Optional[str] = Field(None, max_length=255)
    meta_description: Optional[str] = Field(None, max_length=500)
    tags: Optional[List[str]] = None


class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_in_stock: bool
    is_low_stock: bool
    discount_percentage: Optional[int]
    created_at: datetime
    updated_at: datetime
    category: Optional[CategoryResponse] = None


class ProductListResponse(BaseModel):
    """Slim response for list views — omits heavy fields."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    slug: str
    short_description: Optional[str]
    price: Decimal
    compare_at_price: Optional[Decimal]
    discount_percentage: Optional[int]
    thumbnail_url: Optional[str]
    is_in_stock: bool
    is_low_stock: bool
    is_featured: bool
    category: Optional[CategoryResponse] = None
    created_at: datetime


# ── Pagination / filter params ────────────────────────────────────────────────

class ProductFilterParams(BaseModel):
    q: Optional[str] = Field(None, description="Full-text search query")
    category_id: Optional[int] = None
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)
    in_stock: Optional[bool] = None
    is_featured: Optional[bool] = None
    tags: Optional[List[str]] = Field(None)
    sort_by: str = Field(default="created_at", pattern=r"^(created_at|price|name|updated_at)$")
    sort_order: str = Field(default="desc", pattern=r"^(asc|desc)$")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class PaginatedProductResponse(BaseModel):
    items: List[ProductListResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool

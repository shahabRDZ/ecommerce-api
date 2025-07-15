"""
Integration tests — Product catalog endpoints.

Covers: listing, filtering, searching, pagination, single-item retrieval,
category creation/listing, and admin create/update/delete.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.models.product import Category, Product


# ── Health / smoke ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


# ── Product list ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_products_empty(client: AsyncClient) -> None:
    response = await client.get("/api/v1/products")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["has_next"] is False
    assert data["has_prev"] is False


@pytest.mark.asyncio
async def test_list_products_with_data(
    client: AsyncClient,
    sample_product: Product,
) -> None:
    response = await client.get("/api/v1/products")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["sku"] == "TEST-001"
    assert item["name"] == "Test Widget Pro"
    assert float(item["price"]) == 49.99


@pytest.mark.asyncio
async def test_list_products_pagination(
    client: AsyncClient,
    multiple_products: list[Product],
) -> None:
    # Page 1 of size 2 → 2 items, has_next=True
    r1 = await client.get("/api/v1/products?page=1&page_size=2")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["page"] == 1
    assert d1["page_size"] == 2
    assert len(d1["items"]) == 2
    assert d1["has_next"] is True
    assert d1["has_prev"] is False

    # Last page
    r2 = await client.get(f"/api/v1/products?page={d1['total_pages']}&page_size=2")
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["has_next"] is False
    assert d2["has_prev"] is True


@pytest.mark.asyncio
async def test_list_products_page_size_capped(client: AsyncClient) -> None:
    response = await client.get("/api/v1/products?page_size=999")
    assert response.status_code == 422  # exceeds max


@pytest.mark.asyncio
async def test_list_products_filter_by_category(
    client: AsyncClient,
    sample_product: Product,
    sample_category: Category,
) -> None:
    r = await client.get(f"/api/v1/products?category_id={sample_category.id}")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1

    r2 = await client.get("/api/v1/products?category_id=9999")
    assert r2.status_code == 200
    assert r2.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_products_filter_by_price_range(
    client: AsyncClient,
    multiple_products: list[Product],
) -> None:
    r = await client.get("/api/v1/products?min_price=20&max_price=30")
    assert r.status_code == 200
    items = r.json()["items"]
    for item in items:
        assert 20 <= float(item["price"]) <= 30


@pytest.mark.asyncio
async def test_list_products_filter_in_stock(
    client: AsyncClient,
    sample_product: Product,
    out_of_stock_product: Product,
) -> None:
    r_in = await client.get("/api/v1/products?in_stock=true")
    assert r_in.status_code == 200
    assert r_in.json()["total"] == 1

    r_out = await client.get("/api/v1/products?in_stock=false")
    assert r_out.status_code == 200
    assert r_out.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_products_search(
    client: AsyncClient,
    sample_product: Product,
    multiple_products: list[Product],
) -> None:
    r = await client.get("/api/v1/products?q=widget+pro")
    assert r.status_code == 200
    data = r.json()
    assert any("Widget Pro" in item["name"] for item in data["items"])


@pytest.mark.asyncio
async def test_list_products_sort_by_price_asc(
    client: AsyncClient,
    multiple_products: list[Product],
) -> None:
    r = await client.get("/api/v1/products?sort_by=price&sort_order=asc")
    assert r.status_code == 200
    prices = [float(item["price"]) for item in r.json()["items"]]
    assert prices == sorted(prices)


@pytest.mark.asyncio
async def test_list_products_filter_is_featured(
    client: AsyncClient,
    sample_product: Product,
    multiple_products: list[Product],
) -> None:
    r = await client.get("/api/v1/products?is_featured=true")
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(item["is_featured"] for item in items)


# ── Product detail ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_product_by_id(
    client: AsyncClient,
    sample_product: Product,
) -> None:
    r = await client.get(f"/api/v1/products/{sample_product.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(sample_product.id)
    assert body["sku"] == "TEST-001"
    assert body["is_in_stock"] is True
    assert body["discount_percentage"] == 28  # (69.99-49.99)/69.99 * 100


@pytest.mark.asyncio
async def test_get_product_not_found(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/products/{uuid.uuid4()}")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_product_by_slug(
    client: AsyncClient,
    sample_product: Product,
) -> None:
    r = await client.get("/api/v1/products/slug/test-widget-pro")
    assert r.status_code == 200
    assert r.json()["slug"] == "test-widget-pro"


@pytest.mark.asyncio
async def test_get_product_by_slug_not_found(client: AsyncClient) -> None:
    r = await client.get("/api/v1/products/slug/does-not-exist")
    assert r.status_code == 404


# ── Out-of-stock flags ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_out_of_stock_product_flags(
    client: AsyncClient,
    out_of_stock_product: Product,
) -> None:
    r = await client.get(f"/api/v1/products/{out_of_stock_product.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["is_in_stock"] is False
    assert body["is_low_stock"] is False


# ── Categories ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_categories_empty(client: AsyncClient) -> None:
    r = await client.get("/api/v1/categories")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_categories_with_data(
    client: AsyncClient,
    sample_category: Category,
) -> None:
    r = await client.get("/api/v1/categories")
    assert r.status_code == 200
    cats = r.json()
    assert len(cats) == 1
    assert cats[0]["slug"] == "electronics"


@pytest.mark.asyncio
async def test_create_category(client: AsyncClient) -> None:
    payload = {
        "name": "Clothing",
        "slug": "clothing",
        "description": "Apparel and accessories",
        "sort_order": 2,
    }
    r = await client.post("/api/v1/categories", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == "clothing"
    assert body["id"] is not None


@pytest.mark.asyncio
async def test_create_category_duplicate_slug(
    client: AsyncClient,
    sample_category: Category,
) -> None:
    payload = {"name": "Dup", "slug": "electronics"}
    r = await client.post("/api/v1/categories", json=payload)
    assert r.status_code == 409


# ── Admin product CRUD ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_create_product(
    client: AsyncClient,
    sample_category: Category,
) -> None:
    payload = {
        "sku": "ADM-001",
        "name": "Admin Product",
        "slug": "admin-product",
        "price": "99.99",
        "stock_quantity": 25,
        "category_id": sample_category.id,
        "is_active": True,
    }
    r = await client.post("/api/v1/admin/products", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["sku"] == "ADM-001"
    assert body["stock_quantity"] == 25


@pytest.mark.asyncio
async def test_admin_create_product_duplicate_sku(
    client: AsyncClient,
    sample_product: Product,
) -> None:
    payload = {
        "sku": "TEST-001",
        "name": "Duplicate SKU Product",
        "slug": "duplicate-sku-product",
        "price": "19.99",
    }
    r = await client.post("/api/v1/admin/products", json=payload)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_admin_update_product(
    client: AsyncClient,
    sample_product: Product,
) -> None:
    r = await client.patch(
        f"/api/v1/admin/products/{sample_product.id}",
        json={"price": "59.99", "is_featured": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert float(body["price"]) == 59.99
    assert body["is_featured"] is False


@pytest.mark.asyncio
async def test_admin_soft_delete_product(
    client: AsyncClient,
    sample_product: Product,
) -> None:
    r = await client.delete(f"/api/v1/admin/products/{sample_product.id}")
    assert r.status_code == 204

    # Should no longer appear in public listing
    r2 = await client.get("/api/v1/products")
    assert r2.json()["total"] == 0


@pytest.mark.asyncio
async def test_admin_adjust_stock(
    client: AsyncClient,
    sample_product: Product,
) -> None:
    r = await client.patch(
        f"/api/v1/admin/products/{sample_product.id}/stock",
        json={"delta": 50, "reason": "restock"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["stock_quantity"] == 150  # 100 + 50


@pytest.mark.asyncio
async def test_admin_dashboard_returns_metrics(client: AsyncClient) -> None:
    r = await client.get("/api/v1/admin/dashboard")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "total_products", "active_products", "low_stock_products",
        "out_of_stock_products", "total_orders", "pending_orders",
        "revenue_today", "revenue_total",
    ):
        assert key in body

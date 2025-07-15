"""
Integration tests for order endpoints.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_place_order_empty_cart(client: AsyncClient) -> None:
    """Placing an order with an empty/missing cart returns 400."""
    response = await client.post(
        "/api/v1/orders",
        json={
            "shipping_address": {
                "name": "Jane Doe",
                "address_line1": "123 Main St",
                "city": "Springfield",
                "state": "IL",
                "postal_code": "62701",
                "country": "US",
            },
            "payment_method_id": "pm_test_placeholder",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_list_orders_returns_200(client: AsyncClient) -> None:
    response = await client.get("/api/v1/orders")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_get_order_not_found(client: AsyncClient) -> None:
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/orders/{fake_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_order_not_found(client: AsyncClient) -> None:
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.post(f"/api/v1/orders/{fake_id}/cancel")
    assert response.status_code == 404

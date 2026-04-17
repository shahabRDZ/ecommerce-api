"""
Pytest configuration and shared fixtures.

Uses an in-memory SQLite database (via aiosqlite) so tests run without
a real PostgreSQL instance.  Redis calls are patched via unittest.mock.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.product import Category, Product

# ── Event loop ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── In-memory test database ────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DB_URL, echo=False, connect_args={"check_same_thread": False}
)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Mock Redis ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_redis():
    """Patch get_redis so tests don't need a running Redis instance."""
    mock = AsyncMock()
    mock.hgetall.return_value = {}
    mock.hset.return_value = True
    mock.hdel.return_value = 1
    mock.expire.return_value = True
    with (
        patch("app.database.get_redis", return_value=mock),
        patch("app.services.inventory.get_redis", return_value=mock),
    ):
        yield mock


# ── HTTP client ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(mock_redis) -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# ── DB session convenience fixture ─────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


# ── Seeded data fixtures ───────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sample_category(db_session: AsyncSession) -> Category:
    category = Category(
        name="Electronics",
        slug="electronics",
        description="Electronic gadgets and devices",
        is_active=True,
        sort_order=1,
    )
    db_session.add(category)
    await db_session.commit()
    await db_session.refresh(category)
    return category


@pytest_asyncio.fixture
async def sample_product(
    db_session: AsyncSession, sample_category: Category
) -> Product:
    product = Product(
        id=uuid.uuid4(),
        sku="TEST-001",
        name="Test Widget Pro",
        slug="test-widget-pro",
        description="A fully featured test widget for automated testing",
        short_description="Test widget",
        price=Decimal("49.99"),
        compare_at_price=Decimal("69.99"),
        category_id=sample_category.id,
        stock_quantity=100,
        low_stock_threshold=10,
        track_inventory=True,
        is_active=True,
        is_featured=True,
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def out_of_stock_product(
    db_session: AsyncSession, sample_category: Category
) -> Product:
    product = Product(
        id=uuid.uuid4(),
        sku="TEST-OOS",
        name="Out of Stock Item",
        slug="out-of-stock-item",
        price=Decimal("29.99"),
        category_id=sample_category.id,
        stock_quantity=0,
        track_inventory=True,
        is_active=True,
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)
    return product


@pytest_asyncio.fixture
async def multiple_products(
    db_session: AsyncSession, sample_category: Category
) -> list[Product]:
    products = [
        Product(
            id=uuid.uuid4(),
            sku=f"BULK-{i:03d}",
            name=f"Bulk Product {i}",
            slug=f"bulk-product-{i}",
            price=Decimal(f"{10 + i * 5}.99"),
            category_id=sample_category.id,
            stock_quantity=50,
            track_inventory=True,
            is_active=True,
        )
        for i in range(1, 6)
    ]
    db_session.add_all(products)
    await db_session.commit()
    return products

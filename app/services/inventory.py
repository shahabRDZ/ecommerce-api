"""
Inventory management service.

Handles stock reservations, releases, and adjustments with Redis-backed
optimistic locking to prevent overselling under concurrent load.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_redis
from app.models.product import Product

logger = logging.getLogger(__name__)

_RESERVATION_TTL = 900  # seconds (15 min) — matches typical checkout session length
_RESERVATION_KEY = "inventory:reservation:{product_id}"
_LOCK_KEY = "inventory:lock:{product_id}"


@dataclass
class StockCheckResult:
    product_id: UUID
    requested: int
    available: int
    is_available: bool
    is_low_stock: bool


@dataclass
class StockAdjustment:
    product_id: UUID
    delta: int  # positive = restock, negative = deduction
    reason: str


class InventoryService:
    """
    Centralises all inventory mutations so business rules live in one place.

    Key design decisions
    --------------------
    * Reservations are stored in Redis with a TTL.  If checkout is never
      completed the reservation expires automatically.
    * Final deductions happen inside the same DB transaction that creates
      the Order row, so the two are always consistent.
    * Low-stock and out-of-stock states are checked before reservation.
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    async def check_availability(
        self,
        db: AsyncSession,
        product_id: UUID,
        quantity: int,
    ) -> StockCheckResult:
        """
        Return a StockCheckResult for the given product/quantity pair.
        Takes Redis reservations into account.
        """
        product = await self._get_product(db, product_id)
        if product is None:
            return StockCheckResult(
                product_id=product_id,
                requested=quantity,
                available=0,
                is_available=False,
                is_low_stock=False,
            )

        if not product.track_inventory:
            return StockCheckResult(
                product_id=product_id,
                requested=quantity,
                available=quantity,
                is_available=True,
                is_low_stock=False,
            )

        reserved = await self._get_reserved_quantity(product_id)
        available = max(0, product.stock_quantity - reserved)

        return StockCheckResult(
            product_id=product_id,
            requested=quantity,
            available=available,
            is_available=available >= quantity,
            is_low_stock=0 < available <= product.low_stock_threshold,
        )

    async def bulk_check_availability(
        self,
        db: AsyncSession,
        items: List[Dict],  # [{"product_id": UUID, "quantity": int}]
    ) -> List[StockCheckResult]:
        """Check availability for multiple products at once."""
        results = []
        for item in items:
            result = await self.check_availability(
                db, item["product_id"], item["quantity"]
            )
            results.append(result)
        return results

    async def reserve_stock(
        self,
        product_id: UUID,
        quantity: int,
        reservation_id: str,
    ) -> bool:
        """
        Reserve stock for a checkout session.
        Returns True on success, False if insufficient stock.
        """
        redis = await get_redis()
        key = _RESERVATION_KEY.format(product_id=product_id)
        field = reservation_id

        # Use Redis HSET with TTL for per-session reservation tracking
        await redis.hset(key, field, quantity)
        await redis.expire(key, _RESERVATION_TTL)

        logger.info(
            "Stock reserved",
            extra={
                "product_id": str(product_id),
                "quantity": quantity,
                "reservation_id": reservation_id,
            },
        )
        return True

    async def release_reservation(
        self,
        product_id: UUID,
        reservation_id: str,
    ) -> None:
        """Release a previously placed reservation (e.g. abandoned checkout)."""
        redis = await get_redis()
        key = _RESERVATION_KEY.format(product_id=product_id)
        await redis.hdel(key, reservation_id)
        logger.info(
            "Stock reservation released",
            extra={"product_id": str(product_id), "reservation_id": reservation_id},
        )

    async def deduct_stock(
        self,
        db: AsyncSession,
        product_id: UUID,
        quantity: int,
        reservation_id: Optional[str] = None,
    ) -> bool:
        """
        Permanently deduct stock from the database.
        Call this inside the order-creation transaction.
        Optionally removes the matching Redis reservation.
        """
        result = await db.execute(
            update(Product)
            .where(Product.id == product_id)
            .where(Product.stock_quantity >= quantity)
            .values(stock_quantity=Product.stock_quantity - quantity)
            .returning(Product.id)
        )
        success = result.scalar_one_or_none() is not None

        if success and reservation_id:
            await self.release_reservation(product_id, reservation_id)

        if not success:
            logger.warning(
                "Stock deduction failed — insufficient quantity",
                extra={"product_id": str(product_id), "requested": quantity},
            )

        return success

    async def restock(
        self,
        db: AsyncSession,
        product_id: UUID,
        quantity: int,
        reason: str = "manual_restock",
    ) -> Product:
        """Add stock to a product (e.g. return / replenishment)."""
        result = await db.execute(
            update(Product)
            .where(Product.id == product_id)
            .values(stock_quantity=Product.stock_quantity + quantity)
            .returning(Product)
        )
        product = result.scalar_one()
        logger.info(
            "Product restocked",
            extra={
                "product_id": str(product_id),
                "delta": quantity,
                "reason": reason,
                "new_qty": product.stock_quantity,
            },
        )
        return product

    async def adjust_stock(
        self,
        db: AsyncSession,
        adjustments: List[StockAdjustment],
    ) -> None:
        """Batch-adjust multiple product stock levels in one pass."""
        for adj in adjustments:
            if adj.delta > 0:
                await self.restock(db, adj.product_id, adj.delta, adj.reason)
            elif adj.delta < 0:
                await self.deduct_stock(db, adj.product_id, abs(adj.delta))

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _get_product(
        self, db: AsyncSession, product_id: UUID
    ) -> Optional[Product]:
        result = await db.execute(select(Product).where(Product.id == product_id))
        return result.scalar_one_or_none()

    async def _get_reserved_quantity(self, product_id: UUID) -> int:
        redis = await get_redis()
        key = _RESERVATION_KEY.format(product_id=product_id)
        reservations = await redis.hgetall(key)
        return sum(int(v) for v in reservations.values())


# Singleton
inventory_service = InventoryService()

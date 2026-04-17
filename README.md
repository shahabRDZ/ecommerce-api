# E-Commerce REST API

A production-ready e-commerce backend built with **FastAPI**, **PostgreSQL**, **Redis**, and **Stripe**. Fully Dockerised with a multi-stage build, Nginx reverse proxy, and a GitHub Actions CI/CD pipeline.

[![CI Pipeline](https://github.com/shahabRDZ/ecommerce-api/actions/workflows/ci.yml/badge.svg)](https://github.com/shahabRDZ/ecommerce-api/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Features

- **Product catalog** — full-text search, category filtering, price range, stock status, sorting, and cursor-safe pagination
- **Shopping cart** — works for both authenticated users and anonymous guests (session-cookie based); coupon code support
- **Order management** — cart-to-order conversion with automatic stock deduction; order history and cancellation
- **Stripe integration** — PaymentIntent flow with server-side confirmation; webhook handler for `payment_intent.succeeded` / `payment_intent.payment_failed`
- **Inventory service** — Redis-backed reservation system prevents overselling under high concurrency; automatic TTL release for abandoned checkouts
- **Admin dashboard** — product CRUD, stock adjustment, order status workflow (pending → confirmed → shipped → delivered), and revenue metrics
- **Async throughout** — SQLAlchemy 2.0 async ORM, asyncpg driver, aioredis
- **Observability** — structured JSON request logging with X-Request-ID correlation; optional Sentry SDK integration
- **Rate limiting** — SlowAPI (60 req/min default, 10 req/min on auth routes)
- **Tests** — pytest + httpx async client; SQLite in-memory DB so no Docker required to run tests

---

## Architecture

```
                        ┌──────────────────────────────────────────────┐
                        │                Docker Network                 │
                        │                                               │
  Client ──────────────>│  Nginx :80         FastAPI App :8000          │
  Browser / Mobile      │  ─────────────>    ────────────────────────> │──> Stripe API
                        │  Rate limit         /api/v1/*                 │    (Payments)
                        │  Gzip               Pydantic v2 validation    │
                        │  Security headers   Async SQLAlchemy 2.0      │
                        │                     ──────────────┬───────── │
                        │                                   │           │
                        │              ┌────────────────────┤           │
                        │              v                    v           │
                        │   PostgreSQL 16 :5432      Redis 7 :6379      │
                        │   ─────────────────        ──────────────     │
                        │   Users                    Cart sessions      │
                        │   Products / Categories    Stock reservations │
                        │   Orders / OrderItems      Rate limit counters│
                        │   Carts / CartItems                           │
                        └──────────────────────────────────────────────┘
```

### Entity-Relationship Overview

```
User ──< Order ──< OrderItem >── Product >── Category
 │
 └──── Cart ──< CartItem >── Product
```

**User** has many **Orders** and one **Cart**.
**Order** has many **OrderItems** (product snapshot at purchase time — denormalised for historical accuracy).
**Cart** has many **CartItems** pointing to live **Products**.
**Product** belongs to a **Category** (self-referencing tree for subcategories).

Key design decisions:
- `OrderItem` stores `product_name`, `product_sku`, and `unit_price` as a snapshot — updates to the product catalogue do not alter past orders.
- `CartItem.unit_price` is set when an item is added, allowing comparison with the live price at checkout.
- Stock deductions happen **inside the same DB transaction** that creates the `Order` row, so they are always atomic.

---

## API Reference

### Products  `GET /api/v1/products`

| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string | Full-text search (name, SKU, description) |
| `category_id` | int | Filter by category |
| `min_price` | decimal | Minimum price |
| `max_price` | decimal | Maximum price |
| `in_stock` | bool | `true` = only in-stock items |
| `is_featured` | bool | Filter featured products |
| `sort_by` | string | `created_at` \| `price` \| `name` \| `updated_at` |
| `sort_order` | string | `asc` \| `desc` |
| `page` | int | Page number (1-based) |
| `page_size` | int | Items per page (max 100) |

**Response** — `PaginatedProductResponse`:
```json
{
  "items": [ { "id": "...", "sku": "...", "name": "...", "price": "49.99", ... } ],
  "total": 42,
  "page": 1,
  "page_size": 20,
  "total_pages": 3,
  "has_next": true,
  "has_prev": false
}
```

---

### Full Endpoint Table

#### Products & Categories
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/products` | Paginated list with filters & search |
| `GET` | `/api/v1/products/{id}` | Product detail |
| `GET` | `/api/v1/products/slug/{slug}` | Product by URL slug |
| `GET` | `/api/v1/categories` | List active categories |
| `POST` | `/api/v1/categories` | Create category |

#### Shopping Cart
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/cart` | Get current cart |
| `GET` | `/api/v1/cart/summary` | Lightweight count + total |
| `POST` | `/api/v1/cart/items` | Add item (body: `{product_id, quantity}`) |
| `PATCH` | `/api/v1/cart/items/{id}` | Update quantity |
| `DELETE` | `/api/v1/cart/items/{id}` | Remove item |
| `DELETE` | `/api/v1/cart` | Clear entire cart |
| `POST` | `/api/v1/cart/coupon` | Apply coupon code |
| `DELETE` | `/api/v1/cart/coupon` | Remove coupon |

#### Orders
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/orders` | Place order → returns Stripe `client_secret` |
| `GET` | `/api/v1/orders` | Order history (paginated) |
| `GET` | `/api/v1/orders/{id}` | Order detail |
| `POST` | `/api/v1/orders/{id}/cancel` | Cancel pending/confirmed order |
| `POST` | `/api/v1/webhooks/stripe` | Stripe webhook receiver |

#### Admin
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/admin/dashboard` | Revenue, order counts, stock alerts |
| `GET` | `/api/v1/admin/products` | All products (admin filters) |
| `POST` | `/api/v1/admin/products` | Create product |
| `PATCH` | `/api/v1/admin/products/{id}` | Update product |
| `DELETE` | `/api/v1/admin/products/{id}` | Soft-delete |
| `PATCH` | `/api/v1/admin/products/{id}/stock` | Adjust stock (`delta` ± int) |
| `GET` | `/api/v1/admin/orders` | All orders with status filter |
| `GET` | `/api/v1/admin/orders/{id}` | Order detail |
| `PATCH` | `/api/v1/admin/orders/{id}/status` | Update status + tracking |

---

## Quick Start

### Prerequisites

- Docker ≥ 24 and Docker Compose v2
- A Stripe account (test-mode keys are sufficient)

### 1. Clone & configure

```bash
git clone https://github.com/shahabRDZ/ecommerce-api.git
cd ecommerce-api
cp .env.example .env
```

Edit `.env` — at minimum set:
```bash
SECRET_KEY=<random 32+ char string>
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

### 2. Start the full stack

```bash
docker compose up --build
```

| Service | URL |
|---------|-----|
| API (via Nginx) | http://localhost |
| API (direct) | http://localhost:8000 |
| Interactive docs | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |

### 3. Local development (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Start Postgres and Redis (e.g. via homebrew or docker run)
uvicorn app.main:app --reload --port 8000
```

---

## Running Tests

Tests use SQLite in-memory — no external services required.

```bash
pip install -r requirements.txt aiosqlite
pytest tests/ -v --cov=app --cov-report=term-missing
```

The CI pipeline runs the full test suite against Python 3.11 and 3.12.

---

## Project Structure

```
ecommerce-api/
├── app/
│   ├── main.py               # Application factory, middleware, routers
│   ├── config.py             # Pydantic Settings (reads .env)
│   ├── database.py           # Async SQLAlchemy engine + Redis client
│   ├── models/
│   │   ├── user.py           # User (role, address, Stripe customer ID)
│   │   ├── product.py        # Product + Category (hierarchical)
│   │   ├── cart.py           # Cart + CartItem
│   │   └── order.py          # Order + OrderItem (denormalised snapshot)
│   ├── schemas/
│   │   ├── product.py        # ProductCreate/Update/Response + pagination
│   │   ├── cart.py           # Cart request/response schemas
│   │   └── order.py          # Order placement + status schemas
│   ├── routers/
│   │   ├── products.py       # Catalog endpoints + category CRUD
│   │   ├── cart.py           # Cart management
│   │   ├── orders.py         # Order flow + Stripe webhook
│   │   └── admin.py          # Admin CRUD + dashboard metrics
│   ├── services/
│   │   ├── payment.py        # Stripe PaymentIntent abstraction
│   │   └── inventory.py      # Stock reservation & deduction
│   └── middleware/
│       └── logging.py        # Structured request logging + X-Request-ID
├── tests/
│   ├── conftest.py           # Fixtures, in-memory DB, Redis mock
│   ├── test_products.py      # Product catalog + admin CRUD tests
│   └── test_orders.py        # Order flow + webhook tests
├── nginx/
│   └── nginx.conf            # Reverse proxy, rate limiting, security headers
├── scripts/
│   └── init-db.sql           # PostgreSQL extensions (pg_trgm, uuid-ossp)
├── .github/workflows/
│   └── ci.yml                # Lint → Test → Security → Docker build → Publish
├── Dockerfile                # Multi-stage build (builder → production)
├── docker-compose.yml        # App + PostgreSQL 16 + Redis 7 + Nginx
├── requirements.txt
├── .env.example
└── README.md
```

---

## Key Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://user:pass@host:5432/db` |
| `REDIS_URL` | Yes | `redis://host:6379/0` |
| `SECRET_KEY` | Yes | JWT signing key — 32+ random chars |
| `STRIPE_SECRET_KEY` | Yes | `sk_test_...` or `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | Yes | `whsec_...` from Stripe dashboard |
| `APP_ENV` | No | `development` (default) or `production` |
| `DEBUG` | No | `true` enables SQL echo and verbose logging |
| `SENTRY_DSN` | No | Sentry project DSN for error tracking |
| `RATE_LIMIT_PER_MINUTE` | No | Default `60` |

See [`.env.example`](.env.example) for the complete list.

---

## Payment Flow

```
Frontend                    API                         Stripe
   │                         │                             │
   │  POST /api/v1/orders     │                             │
   │ ─────────────────────>  │  Validate cart + stock      │
   │                         │  Deduct inventory           │
   │                         │  Create Order row           │
   │                         │  PaymentIntent.create() ──> │
   │                         │ <──────────── client_secret │
   │ <─────────────────────  │                             │
   │  {client_secret, ...}   │                             │
   │                         │                             │
   │  confirmCardPayment()   │                             │
   │ ──────────────────────────────────────────────────>  │
   │                         │                             │
   │                         │  <── webhook: succeeded ── │
   │                         │  Order → CONFIRMED/PAID     │
```

---

## Deployment Notes

- **Database migrations** — use [Alembic](https://alembic.sqlalchemy.org). `create_all_tables()` is only called automatically in non-production environments.
- **TLS** — the Nginx config includes a commented-out HTTPS server block. Point your certificates at `/etc/nginx/certs/`.
- **Scaling** — stateless app containers can be horizontally scaled behind Nginx upstream. Redis handles shared session/reservation state.
- **Admin security** — the `/api/v1/admin/*` prefix can be blocked at the Nginx layer for non-internal IPs. Role-based JWT guards should be added when the auth module is wired in.

---

## License

MIT © 2025 shahabRDZ

---

## Join the Discussion

Have ideas or experience to share? Check out our open discussions:

- [Payment failure handling: saga vs pending order](https://github.com/shahabRDZ/ecommerce-api/discussions/27)
- [Cache invalidation for flash sales](https://github.com/shahabRDZ/ecommerce-api/discussions/28)

We'd love to hear your thoughts!
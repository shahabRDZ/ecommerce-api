# E-Commerce API

A production-ready REST API for an e-commerce platform built with **FastAPI**, **PostgreSQL**, **Redis**, and **Stripe**. Fully containerised with Docker Compose and a CI pipeline via GitHub Actions.

## Features

- Product catalog with full-text search, filtering, sorting, and pagination
- Shopping cart — authenticated users and guest sessions (cookie-based)
- Order placement with Stripe PaymentIntent integration
- Webhooks for payment lifecycle events (`payment_intent.succeeded`, etc.)
- Admin dashboard: inventory management, order status updates, metrics
- Redis-backed stock reservations to prevent overselling under concurrent load
- Structured request/response logging with correlation IDs
- Rate limiting (SlowAPI), optional Sentry error tracking
- Async SQLAlchemy 2.0, Pydantic v2, Alembic migrations

---

## API Endpoints

### Products

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/products` | Paginated product list with search & filters |
| GET | `/api/v1/products/{id}` | Product detail |
| GET | `/api/v1/products/slug/{slug}` | Product by slug |
| GET | `/api/v1/categories` | List active categories |
| POST | `/api/v1/categories` | Create category |

### Cart

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/cart` | Get current cart |
| POST | `/api/v1/cart/items` | Add item to cart |
| PATCH | `/api/v1/cart/items/{id}` | Update item quantity |
| DELETE | `/api/v1/cart/items/{id}` | Remove item |
| DELETE | `/api/v1/cart` | Clear cart |
| POST | `/api/v1/cart/coupon` | Apply coupon |
| DELETE | `/api/v1/cart/coupon` | Remove coupon |

### Orders

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/orders` | Place order (returns Stripe client_secret) |
| GET | `/api/v1/orders` | Order history |
| GET | `/api/v1/orders/{id}` | Order detail |
| POST | `/api/v1/orders/{id}/cancel` | Cancel order |
| POST | `/api/v1/webhooks/stripe` | Stripe webhook handler |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/admin/dashboard` | Store metrics |
| GET | `/api/v1/admin/products` | All products (admin view) |
| POST | `/api/v1/admin/products` | Create product |
| PATCH | `/api/v1/admin/products/{id}` | Update product |
| DELETE | `/api/v1/admin/products/{id}` | Soft-delete product |
| PATCH | `/api/v1/admin/products/{id}/stock` | Adjust stock level |
| GET | `/api/v1/admin/orders` | All orders |
| GET | `/api/v1/admin/orders/{id}` | Order detail |
| PATCH | `/api/v1/admin/orders/{id}/status` | Update order status |

---

## Architecture

```
┌─────────┐    ┌───────────────┐    ┌──────────────┐
│  Nginx  │───>│  FastAPI App  │───>│  PostgreSQL  │
│ :80     │    │  :8000        │    │  :5432       │
└─────────┘    └───────────────┘    └──────────────┘
                      │
                      ├──────────────> Redis :6379
                      │               (cart sessions,
                      │                stock reservations)
                      │
                      └──────────────> Stripe API
                                       (payments)
```

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- A Stripe account (test mode keys are fine)

### 1. Clone and configure

```bash
git clone https://github.com/shahabRDZ/ecommerce-api.git
cd ecommerce-api
cp .env.example .env
# Edit .env — set SECRET_KEY and STRIPE_SECRET_KEY at minimum
```

### 2. Start with Docker Compose

```bash
docker compose up --build
```

The API will be available at `http://localhost` (via Nginx) or directly at `http://localhost:8000`.

Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

### 3. Run locally (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit DATABASE_URL and REDIS_URL for local services
uvicorn app.main:app --reload --port 8000
```

---

## Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v --cov=app
```

---

## Environment Variables

See [`.env.example`](.env.example) for the full list. Key variables:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Async PostgreSQL DSN (`postgresql+asyncpg://...`) |
| `REDIS_URL` | Redis connection URL |
| `SECRET_KEY` | JWT signing key (min 32 chars) |
| `STRIPE_SECRET_KEY` | Stripe secret key (`sk_test_...` or `sk_live_...`) |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `APP_ENV` | `development` or `production` |

---

## Project Structure

```
ecommerce-api/
├── app/
│   ├── main.py            # FastAPI application factory
│   ├── config.py          # Pydantic settings
│   ├── database.py        # SQLAlchemy engine, Redis client
│   ├── models/            # ORM models (User, Product, Cart, Order)
│   ├── schemas/           # Pydantic request/response schemas
│   ├── routers/           # Route handlers (products, cart, orders, admin)
│   ├── services/          # Business logic (payment, inventory)
│   └── middleware/        # Request logging middleware
├── tests/                 # pytest integration tests
├── nginx/                 # Nginx reverse proxy config
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## License

MIT

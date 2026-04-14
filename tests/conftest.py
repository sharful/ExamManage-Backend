"""
Shared pytest fixtures for all backend tests.

Test isolation strategy
-----------------------
* Each test gets its own AsyncSession.  All writes are flushed (visible within
  the same session's transaction) but *never committed*, so the database is
  restored to its original state when the session is rolled back after the test.
* The FastAPI app's ``get_db`` dependency is overridden to yield the same
  per-test session, so HTTP-level tests share the identical in-flight
  transaction.
* ``get_current_user`` is overridden to return a fixed admin User — no JWT
  machinery required.

Database
--------
Tests run against the database identified by ``TEST_DATABASE_URL``.  If that
variable is not set the conftest falls back to ``DATABASE_URL`` (loaded from
the project ``.env`` file).  The schema is assumed to already exist (deployed
via Alembic); the tests rely on transaction rollback, not schema recreation.

To point at a dedicated test database:
  export TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname_test
"""

import os
from collections.abc import AsyncGenerator
from datetime import date, datetime, timezone
from pathlib import Path
import uuid

# ── Load .env BEFORE importing any app module ─────────────────────────────────
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env")


def _async_url(url: str) -> str:
    """Ensure the URL uses the asyncpg driver."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


# Default to the same database as production (transaction rollback = no side-effects)
TEST_DATABASE_URL = _async_url(
    os.getenv(
        "TEST_DATABASE_URL",
        os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/exammanage"),
    )
)

# ── Now it is safe to import app modules ──────────────────────────────────────
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.database import get_db
from app.main import app as fastapi_app
from app.middleware.auth import get_current_user
from app.models.exam import Exam, TimeSlot
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.models.room import Room
from app.models.user import User, UserRole

# ── Engine ────────────────────────────────────────────────────────────────────

# NullPool creates a fresh connection per operation — essential for async tests
# where each test may run on a different event loop context.  It avoids the
# "Future attached to a different loop" error that arises when asyncpg reuses
# connections across event loop boundaries.
_test_engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
_TestSessionLocal = async_sessionmaker(
    bind=_test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# ── Per-test database session ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession for the test.  All service calls use ``flush()``
    (not ``commit()``) so writes are visible within the same transaction but
    never reach the database permanently.  The rollback at the end cleans up.

    The mock admin user is inserted at the start of every transaction so that
    audit-log FK constraints are satisfied without requiring a real user row.
    """
    session = _TestSessionLocal()
    try:
        # Insert the stub admin user so audit_logs.user_id FK is satisfied.
        # Use make_transient-safe approach: create a fresh instance each time.
        stub = User(
            id=_MOCK_USER.id,
            username=_MOCK_USER.username,
            email=_MOCK_USER.email,
            password_hash=_MOCK_USER.password_hash,
            role=_MOCK_USER.role,
            is_active=_MOCK_USER.is_active,
            created_at=_MOCK_USER.created_at,
        )
        session.add(stub)
        await session.flush()
        yield session
    finally:
        await session.rollback()
        await session.close()


# ── Stub admin user ───────────────────────────────────────────────────────────

_MOCK_USER = User(
    id=uuid.uuid4(),
    username="test_admin",
    email="test@example.com",
    password_hash="irrelevant",
    role=UserRole.admin,
    is_active=True,
    created_at=datetime.now(timezone.utc),
)


# ── FastAPI test client ───────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    httpx AsyncClient pointed at the FastAPI app with two dependency overrides:

    * ``get_db``           → the per-test transactional session
    * ``get_current_user`` → the stub admin user (no JWT required)
    """

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    fastapi_app.dependency_overrides[get_current_user] = lambda: _MOCK_USER

    transport = ASGITransport(app=fastapi_app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    fastapi_app.dependency_overrides.clear()


# ── Reusable data fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def room(db: AsyncSession) -> Room:
    r = Room(room_number="TEST-101", max_seats=50)
    db.add(r)
    await db.flush()
    return r


@pytest_asyncio.fixture
async def room_small(db: AsyncSession) -> Room:
    r = Room(room_number="TEST-SMALL", max_seats=10)
    db.add(r)
    await db.flush()
    return r


@pytest_asyncio.fixture
async def exam(db: AsyncSession) -> Exam:
    e = Exam(
        exam_name="Test Exam",
        exam_date=date(2026, 6, 15),
        time_slot=TimeSlot.morning,
    )
    db.add(e)
    await db.flush()
    return e


@pytest_asyncio.fixture
async def exam2(db: AsyncSession) -> Exam:
    """Second exam on the same date + slot — for double-booking tests."""
    e = Exam(
        exam_name="Test Exam 2",
        exam_date=date(2026, 6, 15),
        time_slot=TimeSlot.morning,
    )
    db.add(e)
    await db.flush()
    return e


@pytest_asyncio.fixture
async def invigilator(db: AsyncSession) -> Invigilator:
    inv = Invigilator(name="Alice Smith", status=InvigilatorStatus.available)
    db.add(inv)
    await db.flush()
    return inv


@pytest_asyncio.fixture
async def invigilator2(db: AsyncSession) -> Invigilator:
    inv = Invigilator(name="Bob Jones", status=InvigilatorStatus.available)
    db.add(inv)
    await db.flush()
    return inv


@pytest_asyncio.fixture
async def invigilator3(db: AsyncSession) -> Invigilator:
    inv = Invigilator(name="Carol Wang", status=InvigilatorStatus.available)
    db.add(inv)
    await db.flush()
    return inv


@pytest_asyncio.fixture
async def invigilator_unavailable(db: AsyncSession) -> Invigilator:
    inv = Invigilator(name="Dave Sick", status=InvigilatorStatus.unavailable)
    db.add(inv)
    await db.flush()
    return inv

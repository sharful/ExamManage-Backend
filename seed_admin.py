"""
Seed script — creates the default admin user for development.
Usage (from backend/):
    .venv/Scripts/python.exe seed_admin.py
"""
import asyncio

from app.database import AsyncSessionLocal
from app.models.user import UserRole
from app.schemas.user import UserCreate
from app.services.auth_service import create_user, get_user_by_username


async def main() -> None:
    async with AsyncSessionLocal() as db:
        existing = await get_user_by_username(db, "admin")
        if existing:
            print("Admin user already exists — skipping.")
            return

        data = UserCreate(username="admin", password="admin123", role=UserRole.admin)
        user = await create_user(db, data)
        await db.commit()
        print(f"Admin user created: id={user.id}, username={user.username}")


if __name__ == "__main__":
    asyncio.run(main())

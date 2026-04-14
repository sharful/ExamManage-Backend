from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine
from app.routers import auth as auth_router
from app.routers import invigilators as invigilators_router
from app.routers import rooms as rooms_router
from app.routers import exams as exams_router
from app.routers import assignments as assignments_router
from app.routers import dashboard as dashboard_router
from app.routers import reports as reports_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: warm up DB connection pool (non-fatal if DB not yet available)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda _: None)
    except Exception as exc:
        import logging
        logging.getLogger("app").warning("DB not reachable at startup: %s", exc)
    yield
    # Shutdown: dispose engine
    await engine.dispose()


app = FastAPI(
    title="ExamManage API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router.router)
app.include_router(invigilators_router.router)
app.include_router(rooms_router.router)
app.include_router(exams_router.router)
app.include_router(assignments_router.router)
app.include_router(dashboard_router.router)
app.include_router(reports_router.router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

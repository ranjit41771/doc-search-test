import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.dependencies import close_clients, get_db, get_es
from app.routes import auth, documents, health, search
from app.services.db import init_schema
from app.services.search import ensure_index
from app.services.storage import ensure_bucket

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize CockroachDB schema + ES index + S3 bucket
    pool = await get_db()
    await init_schema(pool)

    es = get_es()
    await ensure_index(es)

    try:
        await ensure_bucket()
    except Exception as e:
        log.warning("S3 bucket init failed (LocalStack may not be ready yet): %s", e)

    yield

    await close_clients()


app = FastAPI(
    title="Distributed Document Search Service",
    description=(
        "Enterprise-grade document search with multi-tenancy, "
        "fault tolerance, and horizontal scalability.\n\n"
        "**Architecture**: CockroachDB (source of truth) → RabbitMQ → "
        "Elasticsearch (search index). Redis for caching and rate limiting."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(health.router)

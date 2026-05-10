from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.dependencies import close_clients, get_db, get_es
from app.routes import auth, documents, health, search
from app.services.db import init_schema
from app.services.search import ensure_index


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize CockroachDB schema + ES index
    pool = await get_db()
    await init_schema(pool)

    es = get_es()
    await ensure_index(es)

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

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(health.router)

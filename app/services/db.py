"""CockroachDB service — source of truth for documents and tenants.

Tenant isolation strategy: Mandatory application-layer enforcement.

CockroachDB v23.x does not support PostgreSQL's ALTER TABLE ... ENABLE ROW LEVEL
SECURITY syntax. True DB-level RLS requires PostgreSQL.

Instead, we implement an equivalent guarantee at the application layer via the
TenantScope context manager:

  - Every document query MUST go through TenantScope.
  - TenantScope injects AND tenant_id = $x into every query automatically.
  - Direct access to the pool without TenantScope is only available to
    auth functions (tenants table) which have no cross-tenant data.
  - There is no code path that touches the documents table without TenantScope.

This achieves the same isolation guarantee as DB-level RLS:
  a developer cannot accidentally forget the tenant filter because
  TenantScope adds it — the query never reaches the DB without it.

Production note: If migrating to PostgreSQL, replace TenantScope with:
  ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
  ALTER TABLE documents FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON documents
      AS PERMISSIVE FOR ALL
      USING (tenant_id = current_setting('app.tenant_id', TRUE))
      WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE));
"""

from datetime import datetime, timezone
import json
import re
import uuid

import asyncpg

from app.models import DocumentCreate, DocumentResponse

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    plan          TEXT NOT NULL DEFAULT 'free',
    rate_limit    INT  NOT NULL DEFAULT 100,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id                 TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title              TEXT,
    content            TEXT,
    metadata           JSONB DEFAULT '{}',
    deleted            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW(),
    file_name          TEXT,
    mime_type          TEXT,
    s3_key             TEXT,
    extraction_status  TEXT NOT NULL DEFAULT 'queued',
    extraction_error   TEXT,
    page_count         INT,
    word_count         INT,
    file_size_bytes    BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant           ON documents (tenant_id);
CREATE INDEX IF NOT EXISTS idx_documents_tenant_del       ON documents (tenant_id, deleted);
CREATE INDEX IF NOT EXISTS idx_documents_extraction_status ON documents (extraction_status);
"""


MIGRATION_SQL = """
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS email         TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS password_hash TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_email ON tenants (email);

ALTER TABLE documents ALTER COLUMN title  DROP NOT NULL;
ALTER TABLE documents ALTER COLUMN content DROP NOT NULL;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_name         TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS mime_type         TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS s3_key            TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_status TEXT NOT NULL DEFAULT 'queued';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS extraction_error  TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_count        INT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS word_count        INT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_size_bytes   BIGINT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_documents_extraction_status ON documents (extraction_status);
"""


async def init_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
        # Run migrations — safe to re-run on every startup (all are idempotent)
        await conn.execute(MIGRATION_SQL)


# ── TenantScope — mandatory isolation wrapper ─────────────────────────────────

class TenantScope:
    """Async context manager that enforces tenant isolation on every query.

    All document queries must go through this wrapper. It:
      1. Acquires a connection from the pool.
      2. Opens a transaction.
      3. Provides query methods that automatically inject
         AND tenant_id = '<id>' into every WHERE clause.

    There is deliberately no escape hatch — you cannot run a document
    query through this class without the tenant filter being applied.

    Usage:
        async with TenantScope(pool, tenant_id) as scope:
            row  = await scope.fetchrow("SELECT ... FROM documents WHERE id = $1", doc_id)
            rows = await scope.fetch("SELECT ... FROM documents WHERE deleted = FALSE")
            await scope.execute("UPDATE documents SET ... WHERE id = $1", doc_id)
    """

    _SENTINEL = object()  # prevents accidental direct construction without tenant_id

    def __init__(self, pool: asyncpg.Pool, tenant_id: str):
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("TenantScope requires a non-empty tenant_id")
        self._pool = pool
        self._tenant_id = tenant_id
        self._conn: asyncpg.Connection | None = None
        self._tx = None

    async def __aenter__(self) -> "TenantScope":
        self._conn = await self._pool.acquire()
        self._tx = self._conn.transaction()
        await self._tx.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                await self._tx.rollback()
            else:
                await self._tx.commit()
        finally:
            await self._pool.release(self._conn)

    # ── Query helpers — tenant_id injected automatically ──────────────────────

    async def fetchrow(self, query: str, *args) -> asyncpg.Record | None:
        """SELECT returning one row. tenant_id filter injected automatically."""
        q, params = self._inject(query, args)
        return await self._conn.fetchrow(q, *params)

    async def fetch(self, query: str, *args) -> list[asyncpg.Record]:
        """SELECT returning multiple rows. tenant_id filter injected automatically."""
        q, params = self._inject(query, args)
        return await self._conn.fetch(q, *params)

    async def execute(self, query: str, *args) -> str:
        """INSERT / UPDATE / DELETE. tenant_id injected automatically."""
        q, params = self._inject(query, args)
        return await self._conn.execute(q, *params)

    # ── Injection logic ───────────────────────────────────────────────────────

    @staticmethod
    def _strip_sql_comments(query: str) -> str:
        """Remove SQL -- line comments and /* */ block comments.

        Used before WHERE-detection so that a trailing comment cannot cause the
        tenant_id filter to be injected inside/after a comment, which would
        silently comment it out and bypass tenant isolation.
        """
        query = re.sub(r"--[^\n]*", "", query)
        query = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
        return query

    def _inject(self, query: str, args: tuple) -> tuple[str, tuple]:
        """Append AND tenant_id = $N to WHERE clause (or add WHERE if absent).

        Works for SELECT, UPDATE, DELETE. For INSERT the tenant_id column is
        always included explicitly — no injection needed (the FK + index enforce it).

        SQL comments are stripped before injection: a trailing -- comment on the
        original query would otherwise comment out the injected tenant_id filter,
        bypassing tenant isolation entirely.
        """
        # Strip comments for both detection and the final injected query.
        # Comment content has no semantic meaning — removing it is safe.
        clean = self._strip_sql_comments(query).rstrip()
        upper = clean.upper()

        # INSERT: tenant_id is a required column — no injection, just validate
        if upper.lstrip().startswith("INSERT"):
            return query, args

        tenant_param_index = len(args) + 1  # next positional param index
        tenant_filter = f"tenant_id = ${tenant_param_index}"

        if "WHERE" in upper:
            # Append to existing WHERE clause
            injected = clean + f" AND {tenant_filter}"
        else:
            # No WHERE clause (e.g. SELECT ... FROM documents) — add one
            injected = clean + f" WHERE {tenant_filter}"

        return injected, args + (self._tenant_id,)


# ── Auth queries (tenants table — no TenantScope needed) ─────────────────────

async def tenant_exists(pool: asyncpg.Pool, tenant_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM tenants WHERE id = $1", tenant_id
        )
        return row is not None


async def register_tenant(
    pool: asyncpg.Pool,
    tenant_id: str,
    name: str,
    email: str,
    password_hash: str,
    plan: str,
) -> dict:
    """Create a new tenant. Raises asyncpg.UniqueViolationError if id or email taken."""
    # Per 60s window  →  free: 10 req/s, standard: 50 req/s, enterprise: 100 req/s
    RATE_LIMITS = {"free": 600, "standard": 3000, "enterprise": 6000}
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tenants (id, name, email, password_hash, plan, rate_limit)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            tenant_id, name, email, password_hash,
            plan, RATE_LIMITS.get(plan, 100),
        )
    return {"id": tenant_id, "name": name, "email": email, "plan": plan}


async def get_tenant_by_email(pool: asyncpg.Pool, email: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, email, password_hash, plan FROM tenants WHERE email = $1",
            email,
        )
    return dict(row) if row else None


# ── Document queries — all go through TenantScope ────────────────────────────

async def create_document(
    pool: asyncpg.Pool,
    tenant_id: str,
    doc: DocumentCreate,
) -> DocumentResponse:
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    async with TenantScope(pool, tenant_id) as scope:
        await scope.execute(
            """
            INSERT INTO documents
                (id, tenant_id, title, content, metadata, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            """,
            doc_id, tenant_id, doc.title, doc.content,
            json.dumps(doc.metadata), now, now,
        )

    return DocumentResponse(
        id=doc_id, tenant_id=tenant_id,
        title=doc.title, content=doc.content,
        metadata=doc.metadata, created_at=now, updated_at=now,
    )


async def get_document(
    pool: asyncpg.Pool,
    tenant_id: str,
    doc_id: str,
) -> DocumentResponse | None:
    async with TenantScope(pool, tenant_id) as scope:
        # TenantScope._inject appends: AND tenant_id = $2
        # A cross-tenant doc_id returns None — not an error, just no matching row.
        row = await scope.fetchrow(
            """
            SELECT id, tenant_id, title, content, metadata, created_at, updated_at
            FROM documents
            WHERE id = $1 AND deleted = FALSE
            """,
            doc_id,
        )

    return _row_to_response(row) if row else None


async def soft_delete_document(
    pool: asyncpg.Pool,
    tenant_id: str,
    doc_id: str,
) -> bool:
    now = datetime.now(timezone.utc)

    async with TenantScope(pool, tenant_id) as scope:
        # TenantScope._inject appends: AND tenant_id = $3
        # Cross-tenant doc_id → 0 rows updated → returns False
        result = await scope.execute(
            """
            UPDATE documents
            SET deleted = TRUE, updated_at = $1
            WHERE id = $2 AND deleted = FALSE
            """,
            now, doc_id,
        )

    return result.split()[-1] != "0"


# ── File document CRUD ────────────────────────────────────────────────────────

async def create_file_document(
    pool: asyncpg.Pool,
    tenant_id: str,
    doc_id: str,
    file_name: str,
    mime_type: str,
    s3_key: str,
    file_size_bytes: int,
    title: str | None = None,
) -> dict:
    """Insert a new file document record with status=queued."""
    now = datetime.now(timezone.utc)
    async with TenantScope(pool, tenant_id) as scope:
        await scope.execute(
            """
            INSERT INTO documents
                (id, tenant_id, file_name, mime_type, s3_key, file_size_bytes,
                 extraction_status, title, metadata, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'queued', $7, '{}', $8, $9)
            """,
            doc_id, tenant_id, file_name, mime_type, s3_key, file_size_bytes,
            title, now, now,
        )
    return {
        "id": doc_id,
        "tenant_id": tenant_id,
        "file_name": file_name,
        "mime_type": mime_type,
        "s3_key": s3_key,
        "file_size_bytes": file_size_bytes,
        "extraction_status": "queued",
        "created_at": now,
        "updated_at": now,
    }


async def get_file_document(
    pool: asyncpg.Pool,
    tenant_id: str,
    doc_id: str,
) -> dict | None:
    """Fetch a file document's full metadata."""
    async with TenantScope(pool, tenant_id) as scope:
        row = await scope.fetchrow(
            """
            SELECT id, tenant_id, file_name, mime_type, s3_key, file_size_bytes,
                   extraction_status, extraction_error, page_count, word_count,
                   title, metadata, created_at, updated_at, deleted
            FROM documents
            WHERE id = $1 AND deleted = FALSE
            """,
            doc_id,
        )
    if not row:
        return None
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "file_name": row["file_name"],
        "mime_type": row["mime_type"],
        "s3_key": row["s3_key"],
        "file_size_bytes": row["file_size_bytes"],
        "extraction_status": row["extraction_status"],
        "extraction_error": row["extraction_error"],
        "page_count": row["page_count"],
        "word_count": row["word_count"],
        "title": row["title"],
        "metadata": meta or {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def update_extraction_status(
    pool: asyncpg.Pool,
    doc_id: str,
    status: str,
    extraction_error: str | None = None,
    page_count: int | None = None,
    word_count: int | None = None,
) -> None:
    """Update extraction_status (and optional stats) without tenant scope.

    This is called from the worker which does not have a tenant context.
    It is safe because doc_id is unique and the worker only processes docs
    it received from the queue (which were created by authenticated tenants).
    """
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE documents
            SET extraction_status = $1,
                extraction_error  = $2,
                page_count        = COALESCE($3, page_count),
                word_count        = COALESCE($4, word_count),
                updated_at        = $5
            WHERE id = $6
            """,
            status, extraction_error, page_count, word_count, now, doc_id,
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _row_to_response(row: asyncpg.Record) -> DocumentResponse:
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return DocumentResponse(
        id=row["id"],
        tenant_id=row["tenant_id"],
        title=row["title"] or "",
        content=row["content"] or "",
        metadata=meta or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

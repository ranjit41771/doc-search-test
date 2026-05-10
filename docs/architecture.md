# Distributed Document Search Service — Architecture Design

---

## 1. System Architecture

### High-Level Overview

```
                        ┌──────────────────────────────────────────────────────────┐
                        │                   API Tier (stateless)                   │
Clients ─────────────►  │   FastAPI Pod 1  |  FastAPI Pod 2  |  FastAPI Pod 3      │
                        │         ↑ horizontally scalable — add pods behind LB     │
                        └──────┬────────────────┬──────────────────┬───────────────┘
                               │                │                  │
                  ┌────────────▼───┐   ┌────────▼──────┐   ┌──────▼──────┐
                  │  CockroachDB   │   │ Elasticsearch │   │    Redis    │
                  │ Source of Truth│   │ Search Index  │   │ Cache + RL  │
                  │  CP — Raft     │   │  AP — ~1s lag │   │  AP         │
                  │  RLS enforced  │   │  5 shards     │   │             │
                  └────────┬───────┘   └───────▲───────┘   └─────────────┘
                           │                   │
                           └──► RabbitMQ ──────┘
                                (Index Queue)
                                Index Worker
```

**Key architectural principle**: CockroachDB is the single source of truth. Elasticsearch is a derived, rebuildable search index. This means:
- Document writes are always durable before the API returns
- ES can be wiped and rebuilt from CockroachDB at any time without data loss
- A GET by ID always returns the latest written state (strong consistency)
- Search queries use ES for sub-50ms full-text lookups (eventual consistency, ~1s lag)

---

## 2. Data Flow Diagrams

### Indexing Path (Write)

```
Client
  │
  ├─ POST /documents (X-Tenant-ID: tenant-acme)
  │
  ▼
API Pod
  ├─ 1. Validate tenant → CockroachDB tenants table
  ├─ 2. Check rate limit → Redis INCR (sliding window)
  ├─ 3. Write document → CockroachDB (ACID, returns doc_id)  ← SYNC
  ├─ 4. Publish event  → RabbitMQ index queue               ← ASYNC (fire & forget)
  ├─ 5. Invalidate cache → Redis DEL search:{tenant}:*
  └─ 6. Return 201 Created

RabbitMQ → Index Worker
  ├─ Consume event (delivery_mode=persistent)
  ├─ Write to Elasticsearch (eventual, ~1s lag)
  └─ ACK message
```

### Search Path (Read)

```
Client
  │
  ├─ GET /search?q=distributed+systems (X-Tenant-ID: tenant-acme)
  │
  ▼
API Pod
  ├─ 1. Validate tenant → CockroachDB
  ├─ 2. Check rate limit → Redis
  ├─ 3. Check L2 cache  → Redis GET search:{tenant}:{hash(q)}
  │       └─ HIT  → return cached result (< 5ms)
  │       └─ MISS → continue
  ├─ 4. Query Elasticsearch
  │       ├─ multi_match on title^3 + content
  │       ├─ filter: tenant_id = "tenant-acme"   ← MANDATORY, cached at shard level
  │       ├─ filter: deleted = false
  │       └─ highlight: title + content fragments
  ├─ 5. Cache result → Redis SETEX 60s
  └─ 6. Return SearchResponse (results, total, took_ms, cached=false)
```

---

## 3. Database & Storage Strategy

### CockroachDB — Source of Truth

**Why CockroachDB over PostgreSQL:**
- **Horizontal write scaling**: Distributed Raft consensus, no single-write-primary bottleneck. PostgreSQL's primary is a single node for writes.
- **Same SQL + RLS syntax**: PostgreSQL wire-compatible — identical driver (`asyncpg`), same Row-Level Security:
  ```sql
  ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON documents
      USING (tenant_id = current_setting('app.tenant_id'));
  ```
- **Geo-distribution**: Future data residency compliance — pin tenant data to specific regions via zone configs.
- **ACID across nodes**: Serializable transactions across Raft nodes — critical for quota enforcement race condition prevention.

**Schema:**
```sql
CREATE TABLE tenants (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    plan        TEXT NOT NULL DEFAULT 'free',
    rate_limit  INT  NOT NULL DEFAULT 100,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE documents (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}',
    deleted     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    INDEX idx_documents_tenant     (tenant_id),
    INDEX idx_documents_tenant_del (tenant_id, deleted)
);
```

### Elasticsearch — Search Index

Elasticsearch provides inverted-index full-text search — O(log n) on term lookups regardless of document count. For 10M documents, a properly sharded ES cluster returns results in 50–150ms.

**Index mapping (explicit — no dynamic mapping):**
```json
{
  "settings": { "number_of_shards": 5, "number_of_replicas": 1 },
  "mappings": {
    "properties": {
      "tenant_id": { "type": "keyword" },
      "title":     { "type": "text", "analyzer": "standard", "boost": 3 },
      "content":   { "type": "text", "analyzer": "standard" },
      "metadata":  { "type": "object", "dynamic": false },
      "deleted":   { "type": "boolean" },
      "created_at": { "type": "date" }
    }
  }
}
```

**Tenant isolation in ES** — `tenant_id` is in `filter` context (not `query`), which means:
1. It does NOT affect relevance scoring
2. ES caches filter BitSets at shard level — repeated `tenant_id` filters cost near-zero
3. It is applied before relevance scoring — ES skips non-matching shards entirely

### Redis — Cache + Rate Limiting

| Key pattern | Purpose | TTL |
|---|---|---|
| `search:{tenant_id}:{sha256(q+page+size)[:16]}` | L2 query result cache | 60s |
| `rate:{tenant_id}:{window_ts}` | Sliding window rate limit counter | 70s |

---

## 4. API Design

### Endpoints

| Method | Path | Description | Auth |
|---|---|---|---|
| `POST` | `/documents` | Index a new document | `X-Tenant-ID` header |
| `GET` | `/documents/{id}` | Retrieve document (from CockroachDB) | `X-Tenant-ID` header |
| `DELETE` | `/documents/{id}` | Soft-delete document | `X-Tenant-ID` header |
| `GET` | `/search?q=...&page=1&size=10` | Full-text search (from Elasticsearch) | `X-Tenant-ID` header |
| `GET` | `/health` | Dependency health check | none |

### Request/Response Examples

**POST /documents**
```json
// Request headers: X-Tenant-ID: tenant-acme
// Request body:
{
  "title": "Raft Consensus Algorithm",
  "content": "Raft is a consensus algorithm designed as an alternative to Paxos...",
  "metadata": { "author": "Diego Ongaro", "tags": ["distributed-systems"] }
}

// Response 201:
{
  "id": "01HX9KABC123",
  "tenant_id": "tenant-acme",
  "title": "Raft Consensus Algorithm",
  "content": "Raft is a consensus algorithm...",
  "metadata": { "author": "Diego Ongaro", "tags": ["distributed-systems"] },
  "created_at": "2024-05-09T10:00:00Z",
  "updated_at": "2024-05-09T10:00:00Z"
}
```

**GET /search**
```json
// Request: GET /search?q=consensus+algorithm
// Headers: X-Tenant-ID: tenant-acme

// Response 200:
{
  "query": "consensus algorithm",
  "tenant_id": "tenant-acme",
  "total": 3,
  "took_ms": 12,
  "cached": false,
  "results": [
    {
      "id": "01HX9KABC123",
      "tenant_id": "tenant-acme",
      "title": "Raft Consensus Algorithm",
      "score": 4.2,
      "highlights": {
        "title": ["<em>Raft</em> <em>Consensus</em> <em>Algorithm</em>"],
        "content": ["...designed as an alternative to <em>consensus</em>..."]
      }
    }
  ]
}
```

**GET /health**
```json
{
  "status": "healthy",
  "dependencies": {
    "elasticsearch": { "status": "healthy", "latency_ms": 2.1 },
    "redis":         { "status": "healthy", "latency_ms": 0.8 },
    "cockroachdb":   { "status": "healthy", "latency_ms": 3.4 }
  }
}
```

---

## 5. CAP Theorem & Consistency Model

This system intentionally uses different consistency models for different operations:

| Component | CAP Position | Consistency | Trade-off |
|---|---|---|---|
| **CockroachDB** | **CP** | Serializable (Raft MVCC) | Under partition: refuses writes rather than risk inconsistency. Correct for SoT — data loss is worse than unavailability. |
| **Elasticsearch** | **AP** | Eventual (~1s) | Under partition: serves stale results. Acceptable for search — a 1s indexing lag is not a correctness issue. |
| **Redis** | **AP** | Eventual (gossip) | Under partition: may serve stale cache. Acceptable — cache miss is handled gracefully. |

**PACELC trade-off** (CAP extended to normal operation):
- CockroachDB: favors **C**onsistency over **L**atency even without partition (Raft commit adds ~2ms)
- Elasticsearch: favors **L**atency over **C**onsistency even without partition (1s async index refresh)

**Design implication**: By reading documents from CockroachDB (CP) and searching via Elasticsearch (AP), we achieve both correctness and speed. The eventual consistency window (~1s) for search is a known, documented trade-off — not a data integrity risk.

---

## 6. Multi-Tenancy Strategy

**Model**: Shared Elasticsearch index + mandatory query-time filter + CockroachDB Row-Level Security.

Every document carries `tenant_id`. The tenant filter is injected in a single centralized place (the search service) and cannot be bypassed by route-level bugs:

```python
# services/search.py — tenant_id filter is ALWAYS in the query
"filter": [
    {"term": {"tenant_id": tenant_id}},   # enforced here, not in routes
    {"term": {"deleted": False}},
]
```

For regulated industries (HIPAA, PCI-DSS) requiring stronger isolation: use **index-per-tenant** (`documents_{tenant_id}`). Trade-off: ES recommends max ~1000 indices per cluster, so this limits tenant count.

---

## 7. Caching Strategy

Three cache layers, each with a different hit scenario:

| Layer | Store | Key | TTL | When it hits |
|---|---|---|---|---|
| L1 | In-process LRU | `(tenant_id, query, page, size)` | 10s | Same pod, same query repeated within 10s |
| L2 | Redis | `search:{tenant_id}:{sha256[:16]}` | 60s | Any pod, same query from any client |
| L3 | ES shard BitSet cache | Automatic | LRU | `tenant_id` filter context cached at shard level |

Cache invalidation on write/delete: `DEL search:{tenant_id}:*` — immediate, atomic, prevents stale results for the mutating tenant.

Expected cache hit ratio for search workloads: 60–80% → at 1000 req/s, actual ES load = 200–400 QPS.

---

## 8. Message Queue — Async Indexing

RabbitMQ decouples the write path from ES indexing:

```
POST /documents → CockroachDB (sync, ACID) → API returns 201
                → RabbitMQ publish (fire & forget)
                → Index Worker → Elasticsearch (~1s lag)
```

Benefits:
- **Throughput**: API write speed is limited by CockroachDB (~5ms), not ES (~50ms). 10x write throughput.
- **Backpressure**: Queue absorbs write spikes. Worker processes at ES's capacity.
- **Bulk indexing**: Worker can batch-flush 1000 events to ES in one request.
- **Replayability**: Queue is durable (`delivery_mode=2`). Failed events can be requeued.
- **Rebuild**: Re-enqueue all CockroachDB rows to rebuild ES from scratch after failure.

---

## 9. Production Readiness Analysis

### Scalability — Handling 100x Growth

| Tier | Current | 100x Scale |
|---|---|---|
| API | 3 pods | Add pods behind load balancer — stateless, zero-config scale |
| CockroachDB | 1 node (dev) | 3-node cluster → 9+ nodes (Raft sharding, geo-distribution) |
| Elasticsearch | Single node | 5+ data nodes, 3 master nodes — auto-rebalances shards |
| Redis | Single | Redis Cluster (6 nodes minimum for HA) |
| RabbitMQ | Single | Mirrored queues or switch to Kafka (partitioned, durable) |

CockroachDB horizontal writes: add nodes, Raft automatically rebalances data. No downtime.
ES: `_cat/shards` to identify hot shards; increase `number_of_shards` via index re-creation.

### Resilience

- **Circuit breaker**: `tenacity` library on ES client — exponential backoff, fallback to cache-only mode if ES is down (degraded but operational)
- **Retry**: asyncpg connection pool auto-retries CockroachDB serialization errors (common in distributed transactions)
- **Health checks**: `/health` endpoint polled by load balancer — unhealthy pods removed from rotation
- **RabbitMQ durability**: Messages survive broker restart (`delivery_mode=persistent`, durable queues)
- **Graceful degradation**: If ES is down, document CRUD still works (CRDB is the SoT); search returns 503

### Security

- **Multi-tenancy**: CockroachDB RLS policies (`tenant_isolation`) + application-layer `tenant_id` filter on every ES query — defense in depth
- **Authentication**: In production, add JWT middleware (FastAPI `HTTPBearer`) validating against an auth service. API keys per tenant stored in CockroachDB.
- **Encryption in transit**: TLS between all services (CockroachDB supports TLS natively; ES `xpack.security.enabled=true`)
- **Encryption at rest**: CockroachDB enterprise has at-rest encryption. For open-source: use volume-level encryption (LUKS, cloud KMS)
- **Input validation**: Pydantic models reject malformed requests before they reach service layer
- **Rate limiting**: Per-tenant sliding window prevents one tenant from degrading others (noisy neighbor isolation)

### Observability

- **Metrics**: Prometheus + Grafana — track `search_latency_p95`, `index_queue_depth`, `cache_hit_ratio`, `rate_limit_rejections`
- **Structured logging**: JSON logs with `tenant_id`, `doc_id`, `latency_ms`, `trace_id` on every request
- **Distributed tracing**: OpenTelemetry SDK → Jaeger — trace: API → CockroachDB → RabbitMQ → ES
- **Alerting**: Page on: p95 > 400ms (SLA warning), queue depth > 10k (indexing lag), ES cluster yellow/red, CRDB node down

### Performance Optimization

- **Explicit ES mapping**: No dynamic mapping — ES knows field types before first write, avoids re-indexing
- **ULID as document ID**: Monotonically increasing → sequential ES segment writes → better write throughput than random UUIDs
- **Bulk indexing in worker**: Batch 100–1000 events per ES bulk call → 10x throughput vs. single-doc indexing
- **CockroachDB JSONB indexing**: For high-cardinality metadata fields, add computed column indexes
- **Connection pooling**: asyncpg pool (min 2, max 10) avoids per-request connection overhead

### SLA — 99.95% Availability (4.4 hrs downtime/year)

| Component | Required | How |
|---|---|---|
| API | N+1 pods minimum | Load balancer health checks; rolling deploys |
| CockroachDB | 3-node cluster | Raft tolerates N-1 failures; never 2-node |
| Elasticsearch | 3 master + 3 data nodes | Master quorum requires 3+ nodes |
| Redis | Redis Sentinel (3 nodes) or Redis Cluster | Auto-failover < 30s |
| RabbitMQ | Mirrored queues (3 nodes) | Queue mirrors survive single-node failure |

Zero-downtime deployments: rolling update of API pods (one at a time, health-check before routing traffic).

---

## 10. Experience Showcase

*(To be filled with your personal experience — one paragraph each on:)*

1. A similar distributed system you built and its scale/impact
2. A performance optimization that resulted in significant improvements
3. A critical production incident you resolved in a distributed system
4. An architectural decision that balanced competing concerns

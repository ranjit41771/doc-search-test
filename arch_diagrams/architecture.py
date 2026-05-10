"""
System Design Diagram — Distributed Document Search Service
Shows: write path, search path, async indexing, caching, rate limiting, multi-tenancy.
Run:   python3 arch_diagrams/architecture.py
Output: docs/architecture.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagrams import Cluster, Diagram, Edge
from diagrams.elastic.elasticsearch import Elasticsearch
from diagrams.onprem.client import Users
from diagrams.onprem.database import Cockroachdb
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.network import Nginx
from diagrams.onprem.queue import RabbitMQ
from diagrams.programming.framework import FastAPI


def generate():
    graph_attr = {
        "fontsize": "13",
        "bgcolor": "white",
        "pad": "0.75",
        "splines": "ortho",
        "nodesep": "0.8",
        "ranksep": "1.2",
    }

    with Diagram(
        "Distributed Document Search Service — System Design",
        filename="docs/architecture",
        outformat="png",
        show=False,
        direction="TB",
        graph_attr=graph_attr,
    ):

        # ── Clients ───────────────────────────────────────────────────────────
        clients = Users("Clients\n(X-Tenant-ID header)")

        # ── API Tier ──────────────────────────────────────────────────────────
        with Cluster("API Tier  —  Stateless / Horizontally Scalable"):
            lb = Nginx("Load Balancer")
            with Cluster("FastAPI Pods  (async, uvicorn)"):
                api1 = FastAPI("Pod 1\nTenant Middleware\nRate Limiter\nL1 Cache")
                api2 = FastAPI("Pod 2\nTenant Middleware\nRate Limiter\nL1 Cache")
                api3 = FastAPI("Pod 3\nTenant Middleware\nRate Limiter\nL1 Cache")

        # ── Data Tier ─────────────────────────────────────────────────────────
        with Cluster("Data Tier"):

            with Cluster("CockroachDB Cluster  —  CP  (Raft consensus)\nSource of Truth"):
                crdb = Cockroachdb(
                    "CockroachDB\nDocuments + Tenants\nACID  |  RLS  |  3-node Raft"
                )

            with Cluster("Elasticsearch Cluster  —  AP  (eventual ~1s)\nSearch Index  (derived, rebuildable)"):
                es = Elasticsearch(
                    "Elasticsearch\n5 shards / 1 replica\nInverted index\nFull-text + Relevance"
                )

            with Cluster("Redis  —  AP\nCache + Rate Limiting"):
                redis = Redis(
                    "Redis\nL2 Query Cache (60s TTL)\nSliding Window Rate Limit\nTenant Config"
                )

        # ── Async Indexing ────────────────────────────────────────────────────
        with Cluster("Async Indexing Pipeline"):
            mq = RabbitMQ(
                "RabbitMQ\nIndex Queue\n(durable, persistent)"
            )
            worker = FastAPI(
                "Index Worker\n(consumes queue\nbulk-writes to ES)"
            )

        # ══ Data Flow Edges ═══════════════════════════════════════════════════

        # ── Inbound traffic ───────────────────────────────────────────────────
        clients >> Edge(label="HTTPS  X-Tenant-ID", color="black") >> lb
        lb >> [api1, api2, api3]

        # ── Write path  (POST /documents) ─────────────────────────────────────
        # 1. Validate tenant → CRDB
        # 2. Write document → CRDB  (ACID, source of truth)
        # 3. Publish event  → RabbitMQ  (fire & forget)
        # 4. Invalidate Redis cache
        api1 >> Edge(
            label="① validate tenant\n② write doc (ACID)",
            color="darkgreen", style="bold"
        ) >> crdb

        api1 >> Edge(
            label="③ publish index event\n(async, fire & forget)",
            color="purple", style="dashed"
        ) >> mq

        api1 >> Edge(
            label="④ invalidate cache",
            color="red", style="dashed"
        ) >> redis

        # ── Async indexing  (CRDB → MQ → Worker → ES) ────────────────────────
        mq >> Edge(
            label="consume event",
            color="purple"
        ) >> worker

        worker >> Edge(
            label="bulk index\n(eventual ~1s lag)",
            color="blue", style="bold"
        ) >> es

        # ── Search path  (GET /search) ────────────────────────────────────────
        # 1. Check Redis L2 cache  →  HIT: return cached  /  MISS: query ES
        # 2. Query ES (tenant_id filter in filter context — cached at shard level)
        # 3. Cache result in Redis
        api2 >> Edge(
            label="① check L2 cache\n③ store result (60s TTL)",
            color="red"
        ) >> redis

        api2 >> Edge(
            label="② full-text query\n(tenant_id filter\n+ highlight)",
            color="blue", style="bold"
        ) >> es

        # ── GET /documents/{id}  (strong consistency from CRDB) ───────────────
        api3 >> Edge(
            label="GET by ID\n(strong consistency)",
            color="darkgreen"
        ) >> crdb

        # ── Tenant validation (all pods validate against CRDB) ────────────────
        api2 >> Edge(
            label="validate tenant",
            color="darkgreen", style="dotted"
        ) >> crdb

        api3 >> Edge(
            label="validate tenant\n+ rate limit check",
            color="darkgreen", style="dotted"
        ) >> crdb

        # ── Rate limiting (Redis sliding window) ──────────────────────────────
        api2 >> Edge(
            label="rate limit check\n(sliding window)",
            color="red", style="dotted"
        ) >> redis

        api3 >> Edge(
            label="rate limit check\n(sliding window)",
            color="red", style="dotted"
        ) >> redis


if __name__ == "__main__":
    generate()
    print("Diagram saved to docs/architecture.png")

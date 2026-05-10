"""
Search Path Flow Diagram — GET /search
Shows the full journey of a search query with cache hit and miss paths:
  Client → API → Redis (cache) → Elasticsearch → Redis (cache set) → Client

Run:    python3 arch_diagrams/search_path.py
Output: docs/search_path.png
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagrams import Cluster, Diagram, Edge
from diagrams.elastic.elasticsearch import Elasticsearch
from diagrams.onprem.client import Users
from diagrams.onprem.database import Cockroachdb
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.network import Nginx
from diagrams.programming.framework import FastAPI


def generate():
    graph_attr = {
        "fontsize": "13",
        "bgcolor": "white",
        "pad": "0.8",
        "splines": "ortho",
        "nodesep": "0.9",
        "ranksep": "1.1",
    }

    with Diagram(
        "Search Path — GET /search?q=...",
        filename="docs/search_path",
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr,
    ):

        client = Users("Client\nGET /search?q=...\nAuthorization: Bearer <token>")

        with Cluster("API Tier"):
            lb  = Nginx("Load Balancer")
            api = FastAPI("API Pod\n(FastAPI async)")

        with Cluster("① Tenant Validation  [sync]"):
            crdb = Cockroachdb("CockroachDB\nJWT → tenant_id\nvalidate tenant exists")

        with Cluster("② Rate Limit Check  [sync]"):
            redis_rl = Redis("Redis\nINCR rate:{tenant}:{window}\n429 if exceeded")

        with Cluster("③ L2 Cache Lookup  [sync — < 5ms on HIT]"):
            redis_cache = Redis(
                "Redis\nGET search:{tenant}:{sha256(q)}\n"
                "HIT → return immediately (cached=true)\n"
                "MISS → continue to Elasticsearch"
            )

        with Cluster("④ Full-Text Search  [sync — 50–150ms on MISS]"):
            es = Elasticsearch(
                "Elasticsearch\n"
                "multi_match: title^3 + content\n"
                "filter: tenant_id  ← mandatory\n"
                "filter: deleted=false\n"
                "highlight: title + content\n"
                "BM25 relevance ranking"
            )

        with Cluster("⑤ Cache Result  [async]"):
            redis_store = Redis(
                "Redis\nSETEX search:{tenant}:{sha256(q)}\nTTL = 60s"
            )

        # ── Main request flow ──────────────────────────────────────────────────
        client >> Edge(color="black", label="HTTPS GET") >> lb
        lb     >> Edge(color="black") >> api

        # ── Step 1: Tenant validation ──────────────────────────────────────────
        api >> Edge(
            color="darkgreen", style="bold",
            label="① decode JWT\nextract tenant_id"
        ) >> crdb

        # ── Step 2: Rate limit ─────────────────────────────────────────────────
        api >> Edge(
            color="orange", style="bold",
            label="② check rate limit\n(sliding window)"
        ) >> redis_rl

        # ── Step 3: Cache lookup ───────────────────────────────────────────────
        api >> Edge(
            color="red", style="bold",
            label="③ cache lookup\nsha256(tenant+q+page+size)"
        ) >> redis_cache

        # ── Cache HIT path (return immediately) ───────────────────────────────
        redis_cache >> Edge(
            color="green", style="bold",
            label="CACHE HIT\n← 200 OK  cached=true\n< 5ms total"
        ) >> api

        # ── Cache MISS path → Elasticsearch ───────────────────────────────────
        redis_cache >> Edge(
            color="blue", style="dashed",
            label="CACHE MISS\n→ query ES"
        ) >> es

        es >> Edge(
            color="blue", style="bold",
            label="④ results + highlights\nscored by BM25\n50–150ms"
        ) >> api

        # ── Step 5: Store in cache ─────────────────────────────────────────────
        api >> Edge(
            color="red", style="dashed",
            label="⑤ SETEX 60s\nstore result"
        ) >> redis_store

        # ── Final response to client ───────────────────────────────────────────
        api >> Edge(
            color="black", style="bold",
            label="200 OK\ncached=false\ntook_ms + results"
        ) >> client


if __name__ == "__main__":
    generate()
    print("Diagram saved to docs/search_path.png")

"""
Search Path Flow Diagram — GET /search?q=...  (AWS-native)
Step-by-step flow of a search query on AWS:
  Client → Route53 → WAF → ALB → ECS API pod
  → Cognito JWT decode
  → ElastiCache rate limit
  → ElastiCache cache lookup (HIT: return immediately | MISS: continue)
  → Amazon OpenSearch full-text query (BM25 + tenant filter + highlight)
  → RDS metadata + presigned S3 URL per result
  → ElastiCache cache set (60s TTL)
  → 200 with results + download_url

Run:    python3 arch_diagrams/search_path.py
Output: docs/search_path.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.analytics import ElasticsearchService
from diagrams.aws.compute import ECS, Fargate
from diagrams.aws.database import ElastiCache, RDS
from diagrams.aws.network import ELB, Route53
from diagrams.aws.security import Cognito, WAF
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users


def generate():
    graph_attr = {
        "fontsize": "13",
        "bgcolor": "white",
        "pad": "0.8",
        "splines": "ortho",
        "nodesep": "0.9",
        "ranksep": "1.2",
    }

    with Diagram(
        "Search Path — GET /search?q=...  (AWS)",
        filename="docs/search_path",
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr,
    ):

        client = Users(
            "Client\nGET /search?q=...\nAuthorization: Bearer <JWT>\nX-Tenant-ID header"
        )

        # ── Edge ───────────────────────────────────────────────────────────────
        with Cluster("Edge"):
            dns = Route53("Route 53")
            waf = WAF("WAF\nDDoS + SQLi filter")

        # ── API Tier ───────────────────────────────────────────────────────────
        with Cluster("API Tier  (ECS Fargate)"):
            alb = ELB("ALB\nHTTPS :443")
            api = Fargate("ECS API Pod\nFastAPI / uvicorn")

        # ── ① Auth [green] ─────────────────────────────────────────────────────
        with Cluster("① JWT Decode + Cognito Validation  [sync — < 10ms]"):
            cognito = Cognito(
                "Cognito\nJWKS endpoint\nBearer JWT → tenant_id"
            )

        # ── ② Rate limit [orange] ──────────────────────────────────────────────
        with Cluster("② Rate Limit Check  [sync — < 5ms]"):
            redis_rl = ElastiCache(
                "ElastiCache  (Redis)\nINCR rate:{tenant}:{window}\n429 if limit exceeded"
            )

        # ── ③/④ Cache lookup [red] ────────────────────────────────────────────
        with Cluster("③/④ Cache Lookup  [sync]"):
            redis_cache = ElastiCache(
                "ElastiCache  (Redis)\nGET search:{tenant}:{sha256(q+page+size)}\n"
                "HIT  →  200 OK  cached=true  |  < 5ms total\n"
                "MISS  →  continue to OpenSearch"
            )

        # ── ⑤ OpenSearch [blue] ───────────────────────────────────────────────
        with Cluster("⑤ Full-Text Search  [sync — 50–150ms on MISS]"):
            opensearch = ElasticsearchService(
                "Amazon OpenSearch\n"
                "multi_match: title^3 + content\n"
                "filter: tenant_id  ← mandatory\n"
                "filter: deleted=false\n"
                "highlight: title + content\n"
                "collapse: doc_id (top chunk)\n"
                "BM25 relevance ranking"
            )

        # ── ⑥ Metadata + presigned URL [darkgreen] ────────────────────────────
        with Cluster("⑥ Enrich Results  [sync — per result]"):
            rds = RDS(
                "RDS Aurora PostgreSQL\nSELECT metadata\n(title, mime, page_count,\nword_count, status)"
            )
            s3 = S3(
                "S3 Bucket\nGeneratePresignedUrl\n(1hr TTL)\ndownload_url in response"
            )

        # ── ⑦ Cache set [red] ─────────────────────────────────────────────────
        with Cluster("⑦ Cache Result  [async — fire & forget]"):
            redis_store = ElastiCache(
                "ElastiCache  (Redis)\nSETEX search:{tenant}:{sha256(q)}\nTTL = 60s"
            )

        # ══ Edges ═════════════════════════════════════════════════════════════

        # ── Main request flow ──────────────────────────────────────────────────
        client >> Edge(label="HTTPS GET /search?q=...", color="black") >> dns
        dns >> Edge(color="black") >> waf
        waf >> Edge(color="black") >> alb
        alb >> Edge(color="black") >> api

        # ── ① JWT decode via Cognito ──────────────────────────────────────────
        api >> Edge(
            label="① decode Bearer JWT\nextract tenant_id\nCognito JWKS verify",
            color="darkgreen", style="bold"
        ) >> cognito

        # ── ② Rate limit ──────────────────────────────────────────────────────
        api >> Edge(
            label="② INCR rate:{tenant}:{window}\n429 if exceeded",
            color="orange", style="bold"
        ) >> redis_rl

        # ── ③ Cache lookup ────────────────────────────────────────────────────
        api >> Edge(
            label="③ GET search:{tenant}:{sha256(q)}\ncheck cache",
            color="red", style="bold"
        ) >> redis_cache

        # ── Cache HIT → immediate return ──────────────────────────────────────
        redis_cache >> Edge(
            label="CACHE HIT\n④ 200 OK  cached=true\n< 5ms total",
            color="green", style="bold"
        ) >> api

        # ── Cache MISS → OpenSearch ───────────────────────────────────────────
        redis_cache >> Edge(
            label="CACHE MISS\n→ query OpenSearch",
            color="blue", style="dashed"
        ) >> opensearch

        opensearch >> Edge(
            label="⑤ ranked results\n+ highlights + scores\n50–150ms",
            color="blue", style="bold"
        ) >> api

        # ── ⑥ Fetch metadata from RDS + presigned URL from S3 ─────────────────
        api >> Edge(
            label="⑥a fetch doc metadata\n(title, mime, page_count)",
            color="darkgreen", style="dashed"
        ) >> rds

        api >> Edge(
            label="⑥b GeneratePresignedUrl\n(1hr TTL  per result)",
            color="darkgreen", style="dashed"
        ) >> s3

        # ── ⑦ Store result in cache ───────────────────────────────────────────
        api >> Edge(
            label="⑦ SETEX 60s\nstore full result set",
            color="red", style="dashed"
        ) >> redis_store

        # ── Final response ────────────────────────────────────────────────────
        api >> Edge(
            label="200 OK\n{ results: [\n  { doc_id, title, snippet,\n    page_hint, score,\n    download_url (presigned S3),\n    query_time_ms } ],\n  cached: false }",
            color="black", style="bold"
        ) >> client


if __name__ == "__main__":
    generate()
    print("Diagram saved to docs/search_path.png")

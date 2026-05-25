"""
System Design Diagram — Distributed Document Search Service
Shows: write path (file upload), search path, async extraction, caching,
       rate limiting, multi-tenancy.  Labels note local vs AWS equivalents.
Run:   python3 arch_diagrams/architecture.py
Output: docs/architecture.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.elastic.elasticsearch import Elasticsearch
from diagrams.onprem.client import Users
from diagrams.onprem.compute import Server
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.network import Nginx
from diagrams.onprem.queue import RabbitMQ
from diagrams.onprem.storage import Ceph as S3Generic
from diagrams.programming.framework import FastAPI


def generate():
    graph_attr = {
        "fontsize": "13",
        "bgcolor": "white",
        "pad": "0.75",
        "splines": "ortho",
        "nodesep": "0.9",
        "ranksep": "1.3",
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
        clients = Users("Clients\nAuthorization: Bearer <JWT>\nX-Tenant-ID header")

        # ── Edge / Auth ────────────────────────────────────────────────────────
        with Cluster("Auth / Identity  [local: static JWT | AWS: Cognito]"):
            cognito = Server("JWT Issuer\n(Cognito / local JWKS)")

        # ── API Tier ──────────────────────────────────────────────────────────
        with Cluster("API Tier  —  Stateless / Horizontally Scalable\n[local: docker-compose | AWS: ALB → ECS Fargate]"):
            lb = Nginx("Load Balancer\n[local: Nginx | AWS: ALB]")
            with Cluster("FastAPI Pods  (async, uvicorn)"):
                api1 = FastAPI("Pod 1\nTenant Middleware\nRate Limiter")
                api2 = FastAPI("Pod 2\nTenant Middleware\nRate Limiter")
                api3 = FastAPI("Pod 3\nTenant Middleware\nRate Limiter")

        # ── File Storage ───────────────────────────────────────────────────────
        with Cluster("File Storage  [local: LocalStack | AWS: S3]"):
            s3 = S3Generic("S3 Bucket\noriginal files (PDF/DOCX/\nPPTX/XLSX/images/TXT)\nextracted.txt cache")

        # ── Data Tier ─────────────────────────────────────────────────────────
        with Cluster("Data Tier"):
            with Cluster("Metadata DB  [local: Postgres | AWS: RDS Aurora]\nSource of Truth  —  ACID"):
                rds = PostgreSQL(
                    "PostgreSQL / Aurora\ndocuments + tenants\nstatus | page_count | word_count"
                )

            with Cluster("Search Index  [local: Elasticsearch | AWS: OpenSearch]\nAP  (eventual ~1s)"):
                es = Elasticsearch(
                    "OpenSearch / Elasticsearch\n5 shards / 1 replica\nInverted index  BM25\nFull-text + Highlight"
                )

            with Cluster("Cache + Rate Limit  [local: Redis | AWS: ElastiCache]"):
                redis = Redis(
                    "Redis / ElastiCache\nL2 Query Cache (60s TTL)\nSliding Window Rate Limit\n< 5ms HIT"
                )

        # ── Async Extraction Pipeline ─────────────────────────────────────────
        with Cluster("Async Extraction Pipeline\n[local: RabbitMQ + worker | AWS: SQS + Lambda]"):
            mq = RabbitMQ(
                "SQS / RabbitMQ\nExtraction Job Queue\n(durable, at-least-once)"
            )
            extractor = Lambda(
                "Extraction Worker\n[local: container | AWS: Lambda]\npdfplumber + Tesseract OCR\nchunk → bulk index\n~2–30s per file"
            )

        # ══ Data Flow Edges ═══════════════════════════════════════════════════

        # ── Inbound traffic ───────────────────────────────────────────────────
        clients >> Edge(label="HTTPS  Bearer JWT", color="black") >> lb
        lb >> [api1, api2, api3]

        # ── JWT validation (all pods) ─────────────────────────────────────────
        api1 >> Edge(label="① validate JWT", color="darkgreen", style="dotted") >> cognito
        api2 >> Edge(label="① validate JWT", color="darkgreen", style="dotted") >> cognito
        api3 >> Edge(label="① validate JWT", color="darkgreen", style="dotted") >> cognito

        # ── Rate limit (Redis sliding window) ─────────────────────────────────
        api1 >> Edge(label="② rate limit\n(sliding window)", color="orange", style="dotted") >> redis
        api2 >> Edge(label="② rate limit\n(sliding window)", color="orange", style="dotted") >> redis

        # ── Write path  (POST /documents — multipart file upload) ─────────────
        api1 >> Edge(
            label="③ PUT original file\n(multipart → S3 key)",
            color="darkgreen", style="bold"
        ) >> s3

        api1 >> Edge(
            label="④ INSERT metadata\n(status=queued  ACID)",
            color="darkgreen", style="bold"
        ) >> rds

        api1 >> Edge(
            label="⑤ publish extraction job\n(async, fire & forget)",
            color="purple", style="dashed"
        ) >> mq

        # ── Async extraction pipeline ─────────────────────────────────────────
        mq >> Edge(label="⑥ trigger worker\n(SQS event / consume)", color="purple") >> extractor

        extractor >> Edge(label="download file\nfrom S3", color="darkgreen", style="dashed") >> s3

        extractor >> Edge(
            label="⑦ bulk index chunks\n(eventual ~1s–30s)",
            color="blue", style="bold"
        ) >> es

        extractor >> Edge(label="save extracted.txt", color="darkgreen", style="dashed") >> s3

        extractor >> Edge(
            label="⑧ UPDATE status=indexed\npage_count | word_count",
            color="darkgreen", style="bold"
        ) >> rds

        # ── Search path  (GET /search) ────────────────────────────────────────
        api2 >> Edge(
            label="③ cache lookup\n(HIT < 5ms  |  MISS →)",
            color="red", style="bold"
        ) >> redis

        api2 >> Edge(
            label="④ full-text query\n(tenant_id filter + BM25\n+ highlight + collapse)",
            color="blue", style="bold"
        ) >> es

        api2 >> Edge(
            label="⑤ fetch metadata\n+ generate presigned URL",
            color="darkgreen", style="dashed"
        ) >> rds

        api2 >> Edge(
            label="⑥ presigned GET URL\n(1hr TTL from S3)",
            color="darkgreen", style="dashed"
        ) >> s3

        api2 >> Edge(
            label="⑦ cache result\n(SETEX 60s TTL)",
            color="red", style="dashed"
        ) >> redis

        # ── GET /documents/{id}  (strong read from RDS) ───────────────────────
        api3 >> Edge(
            label="GET by ID\n(strong consistency)",
            color="darkgreen"
        ) >> rds


if __name__ == "__main__":
    generate()
    print("Diagram saved to docs/architecture.png")

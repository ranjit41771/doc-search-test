"""
Write Path Flow Diagram — POST /documents
Shows the full journey of a document write:
  Client → API → CockroachDB (sync) → RabbitMQ → Index Worker → Elasticsearch (async)

Run:    python3 arch_diagrams/write_path.py
Output: docs/write_path.png
"""

import os, sys
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
        "pad": "0.8",
        "splines": "ortho",
        "nodesep": "0.9",
        "ranksep": "1.1",
    }

    with Diagram(
        "Write Path — POST /documents",
        filename="docs/write_path",
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr,
    ):

        client = Users("Client\nPOST /documents\nAuthorization: Bearer <token>")

        with Cluster("API Tier"):
            lb  = Nginx("Load Balancer")
            api = FastAPI("API Pod\n(FastAPI async)")

        with Cluster("① Tenant Validation  [sync]"):
            crdb_auth = Cockroachdb("CockroachDB\ntenants table\nJWT → tenant_id")

        with Cluster("② Rate Limit Check  [sync]"):
            redis_rl = Redis("Redis\nINCR rate:{tenant}:{window}\nSliding window")

        with Cluster("③ Write Document  [sync — ACID]"):
            crdb_write = Cockroachdb("CockroachDB\nINSERT documents\nReturns 201 immediately")

        with Cluster("④ Publish Index Event  [async — fire & forget]"):
            mq = RabbitMQ("RabbitMQ\ndocument_index queue\ndurable + persistent")

        with Cluster("⑤ Invalidate Cache  [async]"):
            redis_cache = Redis("Redis\nDEL search:{tenant}:*\nPrevents stale results")

        with Cluster("⑥ Async Index Worker  [eventual ~1s]"):
            worker = FastAPI("Index Worker\nconsumes queue\nbulk writes to ES")
            es     = Elasticsearch("Elasticsearch\n5 shards\nInverted index updated")

        # ── Main request flow ──────────────────────────────────────────────────
        client >> Edge(color="black", label="HTTPS POST") >> lb
        lb     >> Edge(color="black") >> api

        # ── Sync steps (API waits for these before responding) ─────────────────
        api >> Edge(
            color="darkgreen", style="bold",
            label="① validate JWT\nextract tenant_id"
        ) >> crdb_auth

        api >> Edge(
            color="orange", style="bold",
            label="② INCR counter\ncheck limit"
        ) >> redis_rl

        api >> Edge(
            color="blue", style="bold",
            label="③ INSERT (ACID)\nsource of truth"
        ) >> crdb_write

        # ── Response returned to client here (201 Created) ─────────────────────
        crdb_write >> Edge(
            color="blue", style="dashed",
            label="201 Created\n← returned now"
        ) >> api

        # ── Fire & forget after response ───────────────────────────────────────
        api >> Edge(
            color="purple", style="dashed",
            label="④ publish event\n(non-blocking)"
        ) >> mq

        api >> Edge(
            color="red", style="dashed",
            label="⑤ invalidate\ncache keys"
        ) >> redis_cache

        # ── Async worker pipeline ──────────────────────────────────────────────
        mq >> Edge(
            color="purple",
            label="⑥ consume event\n(bulk batch)"
        ) >> worker

        worker >> Edge(
            color="darkblue", style="bold",
            label="index document\n~1s eventual lag"
        ) >> es


if __name__ == "__main__":
    generate()
    print("Diagram saved to docs/write_path.png")

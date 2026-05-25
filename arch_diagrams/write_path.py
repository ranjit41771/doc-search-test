"""
Write Path Flow Diagram — POST /documents  (AWS-native)
Step-by-step flow of a multipart file upload on AWS:
  Client → Route53 → WAF → ALB → ECS API pod
  → Cognito JWT validation
  → ElastiCache rate limit
  → S3 file upload
  → RDS metadata insert (status=queued)
  → SQS publish
  → 201 returned
  → (async) Lambda extraction → OpenSearch index → S3 extracted.txt → RDS update

Run:    python3 arch_diagrams/write_path.py
Output: docs/write_path.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.analytics import ElasticsearchService
from diagrams.aws.compute import ECS, Fargate, Lambda
from diagrams.aws.database import ElastiCache, RDS
from diagrams.aws.integration import SQS
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
        "Write Path — POST /documents  (AWS)",
        filename="docs/write_path",
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr,
    ):

        client = Users(
            "Client\nPOST /documents\nmultipart/form-data\nAuthorization: Bearer <JWT>"
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
        with Cluster("① JWT Validation  [sync — < 10ms]"):
            cognito = Cognito(
                "Cognito\nJWKS endpoint\nJWT → tenant_id"
            )

        # ── ② Rate limit [orange] ──────────────────────────────────────────────
        with Cluster("② Rate Limit Check  [sync — < 5ms]"):
            redis_rl = ElastiCache(
                "ElastiCache  (Redis)\nINCR rate:{tenant}:{window}\n429 if limit exceeded"
            )

        # ── ③ File storage [darkgreen] ─────────────────────────────────────────
        with Cluster("③ File Upload  [sync — ~50–500ms depending on file size]"):
            s3 = S3(
                "S3 Bucket\nPUT original file\n(PDF/DOCX/PPTX/\nXLSX/images/TXT)\nreturns S3 key"
            )

        # ── ④ Metadata write [darkgreen] ──────────────────────────────────────
        with Cluster("④ Metadata Insert  [sync — ACID]"):
            rds = RDS(
                "RDS Aurora PostgreSQL\nINSERT documents\nstatus=queued\n← 201 returned here"
            )

        # ── ⑤ Queue [purple] ──────────────────────────────────────────────────
        with Cluster("⑤ Publish Extraction Job  [async — fire & forget]"):
            sqs = SQS(
                "SQS Queue\nextraction-jobs\n{ doc_id, s3_key,\n  tenant_id, mime_type }"
            )

        # ── ⑥–⑬ Async extraction [purple + blue] ──────────────────────────────
        with Cluster("⑥–⑬  Async Extraction  [~2–30s  |  triggered by SQS]"):
            extractor = Lambda(
                "Lambda\nExtraction Worker\npdfplumber + Tesseract OCR\nchunk text (512 tokens)"
            )
            opensearch = ElasticsearchService(
                "Amazon OpenSearch\nbulk index chunks\ntenant_id + doc_id\nBM25 inverted index"
            )
            s3_extracted = S3(
                "S3 Bucket\nPUT extracted.txt\n(text cache for\nfast re-index)"
            )
            rds_update = RDS(
                "RDS Aurora PostgreSQL\nUPDATE documents\nstatus=indexed\npage_count | word_count"
            )

        # ══ Edges ═════════════════════════════════════════════════════════════

        # ── Main request flow ──────────────────────────────────────────────────
        client >> Edge(label="HTTPS POST\nmultipart/form-data", color="black") >> dns
        dns >> Edge(color="black") >> waf
        waf >> Edge(color="black") >> alb
        alb >> Edge(color="black") >> api

        # ── ① JWT validation ──────────────────────────────────────────────────
        api >> Edge(
            label="① decode Bearer JWT\nextract tenant_id",
            color="darkgreen", style="bold"
        ) >> cognito

        # ── ② Rate limit ──────────────────────────────────────────────────────
        api >> Edge(
            label="② INCR rate:{tenant}:{window}\n429 if exceeded",
            color="orange", style="bold"
        ) >> redis_rl

        # ── ③ Upload file to S3 ────────────────────────────────────────────────
        api >> Edge(
            label="③ stream file bytes → S3\nreturns s3_key",
            color="darkgreen", style="bold"
        ) >> s3

        # ── ④ Insert metadata row ──────────────────────────────────────────────
        api >> Edge(
            label="④ INSERT (ACID)\ndoc_id | s3_key | tenant_id\nstatus=queued",
            color="darkgreen", style="bold"
        ) >> rds

        # ── API returns 201 immediately ────────────────────────────────────────
        rds >> Edge(
            label="201 Created\n{ doc_id,\n  status: \"queued\" }",
            color="darkgreen", style="dashed"
        ) >> api

        api >> Edge(
            label="201 { doc_id,\n  status: \"queued\" }",
            color="black", style="bold"
        ) >> client

        # ── ⑤ Publish extraction job (after response) ──────────────────────────
        api >> Edge(
            label="⑤ publish extraction job\n{ doc_id, s3_key, tenant_id }\n(non-blocking)",
            color="purple", style="dashed"
        ) >> sqs

        # ── ⑥ SQS triggers Lambda ─────────────────────────────────────────────
        sqs >> Edge(
            label="⑥ SQS event source\ntriggers Lambda",
            color="purple", style="bold"
        ) >> extractor

        # ── ⑦ Download file from S3 ───────────────────────────────────────────
        extractor >> Edge(
            label="⑦ GET s3_key\n(original file)",
            color="darkgreen", style="dashed"
        ) >> s3

        # ── ⑧–⑩ Extract, chunk, index ─────────────────────────────────────────
        extractor >> Edge(
            label="⑧ pdfplumber extract\n⑨ Tesseract OCR\n(scanned pages / images)\n⑩ chunk → bulk index",
            color="blue", style="bold"
        ) >> opensearch

        # ── ⑪ Save extracted.txt ──────────────────────────────────────────────
        extractor >> Edge(
            label="⑪ PUT extracted.txt\n(text cache)",
            color="darkgreen", style="dashed"
        ) >> s3_extracted

        # ── ⑫–⑬ Update RDS status ─────────────────────────────────────────────
        extractor >> Edge(
            label="⑫ UPDATE status=indexed\n⑬ page_count | word_count",
            color="darkgreen", style="bold"
        ) >> rds_update


if __name__ == "__main__":
    generate()
    print("Diagram saved to docs/write_path.png")

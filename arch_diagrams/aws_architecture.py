"""
AWS Architecture Diagram — Distributed Document Search Service
Covers: Edge (Route53 + WAF), ECS Fargate API, S3 file storage, SQS + Lambda
        extraction pipeline, Amazon OpenSearch, ElastiCache Redis, RDS Aurora,
        Cognito auth, CloudWatch observability, CI/CD blue-green deployment.
Run:    python3 arch_diagrams/aws_architecture.py
Output: docs/aws_architecture.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECS, ECR, Fargate, Lambda
from diagrams.aws.database import ElastiCache, RDS
from diagrams.aws.analytics import ElasticsearchService
from diagrams.aws.devtools import Codebuild, Codedeploy, Codepipeline
from diagrams.aws.integration import SQS, Eventbridge
from diagrams.aws.management import Cloudwatch
from diagrams.aws.network import ELB, Route53, VPC, APIGateway
from diagrams.aws.security import WAF, Cognito
from diagrams.aws.storage import S3
from diagrams.onprem.vcs import Github


def generate():
    graph_attr = {
        "fontsize": "13",
        "bgcolor": "white",
        "pad": "0.75",
        "splines": "ortho",
        "nodesep": "0.7",
        "ranksep": "1.1",
    }

    with Diagram(
        "AWS Architecture — Document Search Service",
        filename="docs/aws_architecture",
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr,
    ):

        # ── External clients ───────────────────────────────────────────────────
        users = Github("Users / API Clients")

        # ── Auth ───────────────────────────────────────────────────────────────
        cognito = Cognito("Cognito\nJWT Issuer\nUser Pools (multi-tenant)")

        # ── Edge ───────────────────────────────────────────────────────────────
        with Cluster("Edge"):
            dns = Route53("Route 53\nDNS / Health checks")
            waf = WAF("WAF\nDDoS + SQLi + RateLimitRules")

        # ── VPC ───────────────────────────────────────────────────────────────
        with Cluster("VPC  (Multi-AZ  |  us-east-1)"):

            # Public subnet — load balancer
            with Cluster("Public Subnet"):
                alb = ELB("ALB\n(Application Load Balancer)\nHTTPS :443")

            # App tier — ECS Fargate blue/green
            with Cluster("Private Subnet — App Tier"):
                with Cluster("ECS Cluster  (FastAPI pods)"):

                    with Cluster("Target Group: Blue  (ACTIVE)"):
                        blue_svc = ECS("ECS Service  Blue")
                        blue_pod1 = Fargate("Pod 1\nFastAPI / uvicorn")
                        blue_pod2 = Fargate("Pod 2\nFastAPI / uvicorn")

                    with Cluster("Target Group: Green  (STANDBY)"):
                        green_svc = ECS("ECS Service  Green")
                        green_pod1 = Fargate("Pod 1\nFastAPI / uvicorn")
                        green_pod2 = Fargate("Pod 2\nFastAPI / uvicorn")

            # Extraction pipeline
            with Cluster("Private Subnet — Async Extraction Pipeline"):
                sqs = SQS(
                    "SQS Queue\nextraction-jobs\n(at-least-once, DLQ enabled)"
                )
                extractor = Lambda(
                    "Lambda\nExtraction Worker\npdfplumber + Tesseract OCR\nchunk → bulk index\n~2–30s per file"
                )
                eventbridge = Eventbridge(
                    "EventBridge\nScheduled re-index\n+ DLQ alerts"
                )

            # Data tier
            with Cluster("Private Subnet — Data Tier"):
                rds = RDS(
                    "RDS Aurora PostgreSQL\nDocuments + Tenants\nstatus | page_count | word_count\nSource of Truth  (ACID)"
                )
                cache = ElastiCache(
                    "ElastiCache  (Redis)\nL2 Query Cache (60s TTL)\nRate Limiting (sliding window)\n< 5ms HIT"
                )
                opensearch = ElasticsearchService(
                    "Amazon OpenSearch\n5 shards / 1 replica\nBM25 full-text  +  Highlight\ntenant_id filter"
                )

        # ── File Storage (S3 — outside VPC, accessed via VPC endpoint) ─────────
        s3 = S3(
            "S3 Bucket\nOriginal files (PDF/DOCX/\nPPTX/XLSX/images/TXT)\nextracted.txt cache\nPresigned URLs (1hr TTL)"
        )

        # ── Container Registry ────────────────────────────────────────────────
        ecr = ECR("ECR\nContainer Registry\nFastAPI image")

        # ── CI/CD Pipeline ────────────────────────────────────────────────────
        with Cluster("CI/CD Pipeline  —  Blue/Green Deploy"):
            github = Github("GitHub\nSource")
            pipeline = Codepipeline("CodePipeline\nOrchestrator")
            build = Codebuild("CodeBuild\nBuild + Test\n+ Push to ECR")
            artifact = S3("S3\nBuild Artifacts")
            deploy = Codedeploy(
                "CodeDeploy\nBlue/Green Controller\n1. deploy Green\n2. shift ALB traffic\n3. terminate Blue"
            )

        # ── Observability ─────────────────────────────────────────────────────
        cloudwatch = Cloudwatch(
            "CloudWatch\nMetrics + Logs + Alarms\nX-Ray traces"
        )

        # ══ Connections ═══════════════════════════════════════════════════════

        # ── Request traffic ────────────────────────────────────────────────────
        users >> Edge(label="HTTPS", color="black") >> dns
        dns >> Edge(color="black") >> waf
        waf >> Edge(label="port 443", color="black") >> alb

        # ── JWT auth via Cognito ───────────────────────────────────────────────
        alb >> Edge(
            label="① JWT validation\n(Bearer token → Cognito JWKS)",
            color="darkgreen", style="dashed"
        ) >> cognito

        # ── ALB → active blue target group ────────────────────────────────────
        alb >> Edge(label="routes to active\ntarget group", color="green", style="bold") >> blue_svc
        blue_svc >> [blue_pod1, blue_pod2]

        # ── ALB → standby green (cut-over on deploy) ──────────────────────────
        alb >> Edge(label="cut-over on\nblue/green deploy", style="dashed", color="orange") >> green_svc
        green_svc >> [green_pod1, green_pod2]

        # ── Pods → Rate limit ─────────────────────────────────────────────────
        blue_pod1 >> Edge(label="② rate limit check", color="orange", style="dotted") >> cache
        blue_pod2 >> Edge(label="② rate limit check", color="orange", style="dotted") >> cache

        # ── Write path: upload → S3 → SQS ─────────────────────────────────────
        blue_pod1 >> Edge(label="③ PUT file\n(multipart)", color="darkgreen", style="bold") >> s3
        blue_pod1 >> Edge(label="④ INSERT metadata\n(status=queued)", color="darkgreen", style="bold") >> rds
        blue_pod1 >> Edge(label="⑤ publish job\n(extraction msg)", color="purple", style="dashed") >> sqs

        # S3 event notification → SQS (alternative trigger)
        s3 >> Edge(label="S3 Event Notification\n(ObjectCreated)", color="purple", style="dashed") >> sqs

        # ── SQS → Lambda extraction ────────────────────────────────────────────
        sqs >> Edge(label="⑥ triggers Lambda\n(SQS event source)", color="purple", style="bold") >> extractor
        extractor >> Edge(label="download\noriginal file", color="darkgreen", style="dashed") >> s3
        extractor >> Edge(label="⑦ bulk index chunks\n(pdfplumber + OCR)", color="blue", style="bold") >> opensearch
        extractor >> Edge(label="save extracted.txt", color="darkgreen", style="dashed") >> s3
        extractor >> Edge(label="⑧ UPDATE status=indexed\npage_count | word_count", color="darkgreen", style="bold") >> rds

        # ── EventBridge → SQS (scheduled re-index / DLQ alerts) ───────────────
        eventbridge >> Edge(label="scheduled re-index\n/ DLQ alert", color="purple", style="dashed") >> sqs

        # ── Search path ────────────────────────────────────────────────────────
        blue_pod2 >> Edge(label="③ cache lookup\nHIT < 5ms", color="red", style="bold") >> cache
        blue_pod2 >> Edge(label="④ full-text query\n(BM25 + tenant filter)", color="blue", style="bold") >> opensearch
        blue_pod2 >> Edge(label="⑤ fetch metadata\n+ presigned URL", color="darkgreen", style="dashed") >> rds
        blue_pod2 >> Edge(label="⑥ generate\npresigned URL (1hr)", color="darkgreen", style="dashed") >> s3
        blue_pod2 >> Edge(label="⑦ cache result\nSETEX 60s", color="red", style="dashed") >> cache

        # ── CI/CD flow ─────────────────────────────────────────────────────────
        github >> Edge(label="push / PR merge") >> pipeline
        pipeline >> Edge(color="gray") >> build
        build >> Edge(label="push image") >> ecr
        build >> Edge(label="store artifacts") >> artifact
        artifact >> Edge(color="gray") >> deploy
        deploy >> Edge(label="blue/green switch", color="orange", style="bold") >> alb

        # ECS pulls image from ECR on task start
        ecr >> Edge(label="pull image", style="dashed") >> blue_svc
        ecr >> Edge(label="pull image", style="dashed") >> green_svc

        # Lambda pulls image from ECR
        ecr >> Edge(label="pull image", style="dashed") >> extractor

        # ── Observability (all services → CloudWatch) ──────────────────────────
        cloudwatch << Edge(label="metrics + logs") << blue_svc
        cloudwatch << Edge(label="metrics + logs") << green_svc
        cloudwatch << Edge(label="invocation metrics") << extractor
        cloudwatch << Edge(label="DB metrics") << rds
        cloudwatch << Edge(label="cache metrics") << cache
        cloudwatch << Edge(label="search metrics") << opensearch
        cloudwatch << Edge(label="queue metrics") << sqs


if __name__ == "__main__":
    generate()
    print("Diagram saved to docs/aws_architecture.png")

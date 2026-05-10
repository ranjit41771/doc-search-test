"""
AWS Architecture Diagram — Distributed Document Search Service
Covers: Traffic flow, ECS blue-green deployment, CI/CD pipeline, data tier.
Run: python3 arch_diagrams/aws_architecture.py
Output: docs/aws_architecture.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECR, ECS, Fargate
from diagrams.aws.database import ElastiCache, RDS
from diagrams.aws.analytics import ElasticsearchService
from diagrams.aws.devtools import Codebuild, Codedeploy, Codepipeline
from diagrams.aws.management import Cloudwatch
from diagrams.aws.network import ELB, Route53, VPC
from diagrams.aws.security import WAF
from diagrams.aws.storage import S3
from diagrams.onprem.vcs import Github


def generate():
    graph_attr = {
        "fontsize": "13",
        "bgcolor": "white",
        "pad": "0.75",
        "splines": "ortho",
        "nodesep": "0.6",
        "ranksep": "1.0",
    }

    with Diagram(
        "AWS Architecture — Document Search Service",
        filename="docs/aws_architecture",
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=graph_attr,
    ):
        # ── Client ────────────────────────────────────────────────────────────
        users = Github("Users / Clients")

        # ── DNS & Edge Security ───────────────────────────────────────────────
        with Cluster("Edge"):
            dns = Route53("Route 53\n(DNS)")
            waf = WAF("WAF\n(DDoS + SQL injection)")

        # ── VPC ───────────────────────────────────────────────────────────────
        with Cluster("VPC  (Multi-AZ)"):

            # ── Public Subnet ─────────────────────────────────────────────────
            with Cluster("Public Subnet"):
                alb = ELB("ALB\n(Application Load Balancer)")

            # ── ECS Cluster — Blue / Green ────────────────────────────────────
            with Cluster("Private Subnet — App Tier"):
                with Cluster("ECS Cluster"):

                    with Cluster("Target Group: Blue  (ACTIVE)"):
                        blue_svc = ECS("ECS Service\n(Blue)")
                        blue_tasks = [
                            Fargate("Task 1\nAPI Pod"),
                            Fargate("Task 2\nAPI Pod"),
                            Fargate("Task 3\nIndex Worker"),
                        ]

                    with Cluster("Target Group: Green  (STANDBY)"):
                        green_svc = ECS("ECS Service\n(Green)")
                        green_tasks = [
                            Fargate("Task 1\nAPI Pod"),
                            Fargate("Task 2\nAPI Pod"),
                            Fargate("Task 3\nIndex Worker"),
                        ]

            # ── Data Tier ─────────────────────────────────────────────────────
            with Cluster("Private Subnet — Data Tier"):
                db = RDS("Aurora\n(CockroachDB-compatible\nSource of Truth)")
                cache = ElastiCache("ElastiCache\n(Redis — Cache\n+ Rate Limiting)")
                queue = ECS("Amazon MQ\n(RabbitMQ\nIndex Queue)")
                es = ElasticsearchService("Amazon OpenSearch\n(Elasticsearch\nSearch Index)")

        # ── Container Registry ────────────────────────────────────────────────
        ecr = ECR("ECR\n(Container Registry)")

        # ── CI/CD Pipeline ────────────────────────────────────────────────────
        with Cluster("CI/CD Pipeline"):
            github = Github("GitHub\n(Source Trigger)")
            pipeline = Codepipeline("CodePipeline\n(Orchestrator)")
            build = Codebuild("CodeBuild\n(Build + Test\n+ Push to ECR)")
            artifact = S3("S3\n(Build Artifacts)")
            deploy = Codedeploy("CodeDeploy\n(Blue/Green\nDeployment Controller)")

        # ── Observability ─────────────────────────────────────────────────────
        with Cluster("Observability"):
            cloudwatch = Cloudwatch("CloudWatch\n(Metrics + Logs\n+ Alarms)")

        # ══ Connections ═══════════════════════════════════════════════════════

        # Traffic flow: User → Edge → ALB → Blue target group (active)
        users >> Edge(label="HTTPS") >> dns
        dns >> waf
        waf >> Edge(label="port 443") >> alb
        alb >> Edge(label="routes to active\ntarget group", color="green") >> blue_svc
        blue_svc >> blue_tasks

        # Green is standby — ALB will cut over here on deploy
        alb >> Edge(label="cut-over on\nblue/green deploy", style="dashed", color="orange") >> green_svc
        green_svc >> green_tasks

        # Tasks → Data tier
        for task in blue_tasks + green_tasks:
            task >> Edge(color="brown", style="dashed") >> db
            task >> Edge(color="red", style="dashed") >> cache
            task >> Edge(color="purple", style="dashed") >> queue

        # Index Worker (Task 3) reads search queries from ES
        # Queue → ES (async indexing: CRDB write → MQ → worker → ES)
        queue >> Edge(label="index worker\nCRDB → ES sync", color="darkblue") >> es
        # API pods query ES for full-text search
        blue_tasks[0] >> Edge(label="search queries", color="darkblue", style="dashed") >> es
        blue_tasks[1] >> Edge(label="search queries", color="darkblue", style="dashed") >> es
        green_tasks[0] >> Edge(label="search queries", color="darkblue", style="dashed") >> es
        green_tasks[1] >> Edge(label="search queries", color="darkblue", style="dashed") >> es

        # CI/CD flow: GitHub push → CodePipeline → Build → Deploy
        github >> Edge(label="push / PR merge\ntriggers webhook") >> pipeline
        pipeline >> build
        build >> Edge(label="push image") >> ecr
        build >> Edge(label="store artifacts") >> artifact
        artifact >> deploy
        deploy >> Edge(
            label="1. deploy Green\n2. shift ALB traffic\n3. terminate Blue",
            color="orange",
            style="bold",
        ) >> alb

        # ECS pulls latest image from ECR on task launch
        ecr >> Edge(label="pull image on\ntask start", style="dashed") >> blue_svc
        ecr >> Edge(label="pull image on\ntask start", style="dashed") >> green_svc

        # Observability
        cloudwatch << Edge(label="metrics + logs") << blue_svc
        cloudwatch << Edge(label="metrics + logs") << green_svc
        cloudwatch << Edge(label="DB metrics") << db
        cloudwatch << Edge(label="cache metrics") << cache
        cloudwatch << Edge(label="ES metrics") << es


if __name__ == "__main__":
    generate()
    print("Diagram saved to docs/aws_architecture.png")

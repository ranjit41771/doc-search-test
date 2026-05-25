"""
Lambda Extraction Pipeline Diagram
Shows the internal step-by-step process inside the Lambda extraction worker —
from receiving an SQS event to saving searchable chunks in OpenSearch.

Run:    python3 arch_diagrams/lambda_extraction_pipeline.py
Output: docs/lambda_extraction_pipeline.png
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.storage import S3
from diagrams.aws.integration import SQS
from diagrams.aws.analytics import ElasticsearchService
from diagrams.aws.database import RDS
from diagrams.onprem.compute import Server
from diagrams.programming.language import Python


def generate():
    graph_attr = {
        "fontsize": "12",
        "bgcolor": "white",
        "pad": "1.0",
        "splines": "ortho",
        "nodesep": "0.7",
        "ranksep": "1.2",
    }

    with Diagram(
        "Lambda Extraction Pipeline — Document Search Service",
        filename="docs/lambda_extraction_pipeline",
        outformat="png",
        show=False,
        direction="TB",
        graph_attr=graph_attr,
    ):

        # ── ① SQS Trigger (outside Lambda) ───────────────────────────────────
        sqs = SQS(
            "① SQS Trigger\nextraction-jobs\n{ doc_id, tenant_id, s3_key }\n(at-least-once, DLQ enabled)"
        )

        with Cluster("Lambda Extraction Worker"):

            # ── ② Download ────────────────────────────────────────────────────
            s3_download = S3(
                "② Download from S3\nboto3 get_object\nraw bytes in memory\n~50–200ms"
            )

            # ── ③ MIME Detection ──────────────────────────────────────────────
            mime_detect = Server(
                "③ MIME Detection\npython-magic (actual bytes)\nvalidate vs allowlist\n< 5ms"
            )

            # ── ④ Route ───────────────────────────────────────────────────────
            route = Python(
                "④ Route by File Type\ndispatch to type-specific extractor"
            )

            # ── ⑤–⑨ File Type Extractors ─────────────────────────────────────
            with Cluster("⑤–⑨  File Type Extractors  (route by MIME)"):
                pdf_ext = Server(
                    "⑤ PDF\npdfplumber\nper-page text extraction\n~100ms/page"
                )
                docx_ext = Server(
                    "⑥ DOCX\npython-docx\nparagraphs + tables\n+ inline_shapes\n~200ms"
                )
                pptx_ext = Server(
                    "⑦ PPTX\npython-pptx\nper-slide text frames\n+ slide images\n~200ms"
                )
                xlsx_ext = Server(
                    "⑧ XLSX / CSV\nopenpyxl\ncell values per sheet\n~50ms"
                )
                txt_ext = Server(
                    "⑨ TXT / MD\nchardet encoding detect\ndecode → UTF-8\n< 10ms"
                )

            # ── ⑪ Tesseract OCR ───────────────────────────────────────────────
            with Cluster("⑪  Tesseract OCR Engine  (CPU-heavy, thread executor)"):
                ocr = Server(
                    "⑪ Tesseract OCR\npytesseract.image_to_string\nlang=eng+osd\nrun_in_executor (non-blocking)\n2–30s/page"
                )

            # ── ⑩ Combine ─────────────────────────────────────────────────────
            combine = Python(
                "⑩ Combine Page Blocks\nList[{ page_number, text }]\nmerge all page/slide output"
            )

            # ── ⑫ Chunking ────────────────────────────────────────────────────
            chunking = Python(
                "⑫ Text Chunking\n~500 char chunks\n100 char overlap (sliding window)\nList[{ chunk_index, page_number, text }]\n< 10ms"
            )

        # ── ⑬–⑮ Output (outside Lambda cluster for clarity) ──────────────────
        with Cluster("⑬–⑮  Persist Results"):
            opensearch = ElasticsearchService(
                "⑬ Bulk Index → OpenSearch\nhelpers.bulk() per chunk:\n{ doc_id, chunk_index, page_number,\n  tenant_id, file_name, text, s3_key }\n~200ms"
            )
            s3_cache = S3(
                "⑭ Save extracted.txt → S3\n{tenant}/docs/{doc_id}/extracted.txt\navoids re-extraction on re-index\n~100ms"
            )
            rds_success = RDS(
                "⑮ Update RDS Metadata\nstatus = 'indexed'\npage_count, word_count\nextraction_error = NULL\n~20ms"
            )

        # ── Error Path ────────────────────────────────────────────────────────
        with Cluster("Error Handler  (any step throws exception)"):
            error_catch = Server(
                "Catch Exception\nstr(e) captured\nSQS msg deleted → DLQ"
            )
            rds_error = RDS(
                "Update RDS\nstatus = 'failed'\nextraction_error = str(e)\nfile intact in S3\n(downloadable, not searchable)"
            )

        # ══ Edges ═════════════════════════════════════════════════════════════

        # ── ① SQS → ② Download ───────────────────────────────────────────────
        sqs >> Edge(
            label="① SQS event source triggers Lambda\n{ doc_id, tenant_id, s3_key }",
            color="purple", style="bold"
        ) >> s3_download

        # ── ② → ③ MIME Detect ────────────────────────────────────────────────
        s3_download >> Edge(
            label="② raw bytes in memory",
            color="darkblue", style="bold"
        ) >> mime_detect

        # ── ③ → ④ Route ───────────────────────────────────────────────────────
        mime_detect >> Edge(
            label="③ validated MIME type",
            color="black", style="bold"
        ) >> route

        # ── ④ Route → Extractors ──────────────────────────────────────────────
        route >> Edge(label="PDF", color="gray", style="bold") >> pdf_ext
        route >> Edge(label="DOCX", color="gray", style="bold") >> docx_ext
        route >> Edge(label="PPTX", color="gray", style="bold") >> pptx_ext
        route >> Edge(label="XLSX/CSV", color="gray", style="bold") >> xlsx_ext
        route >> Edge(label="TXT/MD", color="gray", style="bold") >> txt_ext

        # ── Route → OCR (direct: PNG / JPG / TIFF) ───────────────────────────
        route >> Edge(
            label="IMG (PNG/JPG/TIFF)\ndirect to OCR ⑪",
            color="darkorange", style="bold"
        ) >> ocr

        # ── PDF → OCR (image-only / empty pages) ─────────────────────────────
        pdf_ext >> Edge(
            label="⑤ page text empty?\npdf2image → PIL Image",
            color="darkorange", style="dashed"
        ) >> ocr

        # ── DOCX → OCR (embedded images) ─────────────────────────────────────
        docx_ext >> Edge(
            label="⑥ inline_shapes\nembedded image bytes",
            color="darkorange", style="dashed"
        ) >> ocr

        # ── PPTX → OCR (slide images) ─────────────────────────────────────────
        pptx_ext >> Edge(
            label="⑦ shape.image.blob\nslide picture shapes",
            color="darkorange", style="dashed"
        ) >> ocr

        # ── Extractors → ⑩ Combine ───────────────────────────────────────────
        pdf_ext >> Edge(label="page text", color="darkgreen") >> combine
        docx_ext >> Edge(label="doc text", color="darkgreen") >> combine
        pptx_ext >> Edge(label="slide text", color="darkgreen") >> combine
        xlsx_ext >> Edge(label="sheet rows", color="darkgreen") >> combine
        txt_ext >> Edge(label="decoded text", color="darkgreen") >> combine

        # ── ⑪ OCR → ⑩ Combine ────────────────────────────────────────────────
        ocr >> Edge(
            label="⑪ OCR text appended\nto page block",
            color="darkorange", style="bold"
        ) >> combine

        # ── ⑩ Combine → ⑫ Chunking ───────────────────────────────────────────
        combine >> Edge(
            label="⑩ List[{ page_number, raw_text }]",
            color="black", style="bold"
        ) >> chunking

        # ── ⑫ Chunking → ⑬ OpenSearch ────────────────────────────────────────
        chunking >> Edge(
            label="⑫ List[{ chunk_index, page_number, text }]\nbulk index ⑬",
            color="blue", style="bold"
        ) >> opensearch

        # ── ⑫ Chunking → ⑭ S3 Cache ──────────────────────────────────────────
        chunking >> Edge(
            label="⑭ full text concatenated\n→ extracted.txt",
            color="darkblue", style="dashed"
        ) >> s3_cache

        # ── ⑬ OpenSearch → ⑮ RDS success update ─────────────────────────────
        opensearch >> Edge(
            label="⑮ after successful bulk index",
            color="darkgreen", style="bold"
        ) >> rds_success

        # ── Error path (dashed red) ────────────────────────────────────────────
        # Representative connections from key failure points
        mime_detect >> Edge(
            label="invalid MIME\nor download error",
            color="red", style="dashed"
        ) >> error_catch

        chunking >> Edge(
            label="extraction / OCR\nor index error",
            color="red", style="dashed"
        ) >> error_catch

        error_catch >> Edge(
            label="status='failed'\nextraction_error=str(e)\noriginal file intact in S3",
            color="red", style="bold"
        ) >> rds_error


if __name__ == "__main__":
    generate()
    print("Diagram saved to docs/lambda_extraction_pipeline.png")

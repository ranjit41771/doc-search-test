"""Document routes — file upload, status polling, download, and deletion."""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import RedirectResponse

from app.config import settings
from app.dependencies import get_db, get_es, get_redis
from app.middleware.tenant import get_tenant_id
from app.models import DocumentDetailResponse, DocumentUploadResponse
from app.services import db as db_svc
from app.services import search as search_svc
from app.services import storage as storage_svc
from app.services.cache import invalidate_tenant_cache
from app.services.extractors import ALLOWED_MIME_TYPES
from app.services.queue import publish_index_event
from app.services.rate_limiter import check_rate_limit

log = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_FILE_SIZE_BYTES = settings.max_file_size_mb * 1024 * 1024


async def enforce_rate_limit(tenant_id: str = Depends(get_tenant_id)) -> str:
    redis = get_redis()
    allowed, retry_after = await check_rate_limit(redis, tenant_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return tenant_id


def _detect_mime(file_bytes: bytes) -> str:
    """Use python-magic on actual file bytes (not the client-supplied Content-Type)."""
    try:
        import magic
        return magic.from_buffer(file_bytes, mime=True)
    except Exception:
        return "application/octet-stream"


@router.post("", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(..., description="Document file to upload"),
    title: Optional[str] = Form(None, max_length=500),
    tags: Optional[str] = Form(None, description="Comma-separated tags"),
    tenant_id: str = Depends(enforce_rate_limit),
):
    """Upload a file document.

    Accepts multipart/form-data with a `file` field.
    Returns immediately with status=queued. Poll GET /documents/{id} for status.

    Supported types: PDF, DOCX, PPTX, TXT/MD, PNG/JPG/TIFF, XLSX/CSV.
    Max file size: 50 MB.
    """
    # Read file bytes
    file_bytes = await file.read()
    file_size = len(file_bytes)

    if file_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if file_size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {settings.max_file_size_mb} MB.",
        )

    # Validate MIME type from actual bytes (not client Content-Type)
    mime_type = _detect_mime(file_bytes)
    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type: {mime_type}. "
                f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}"
            ),
        )

    doc_id = str(uuid.uuid4())
    file_name = file.filename or "upload"
    s3_key = f"{tenant_id}/docs/{doc_id}/{file_name}"

    # Upload to S3
    try:
        await storage_svc.upload_file(file_bytes, s3_key, content_type=mime_type)
    except Exception as e:
        log.error("S3 upload failed for doc %s: %s", doc_id, e)
        raise HTTPException(status_code=500, detail="Failed to store file. Please try again.")

    # Insert DB row with status=queued
    pool = await get_db()
    await db_svc.create_file_document(
        pool=pool,
        tenant_id=tenant_id,
        doc_id=doc_id,
        file_name=file_name,
        mime_type=mime_type,
        s3_key=s3_key,
        file_size_bytes=file_size,
        title=title,
    )

    # Publish extraction job to RabbitMQ
    await publish_index_event({
        "action": "extract",
        "doc_id": doc_id,
        "tenant_id": tenant_id,
        "s3_key": s3_key,
        "file_name": file_name,
        "mime_type": mime_type,
    })

    redis = get_redis()
    await invalidate_tenant_cache(redis, tenant_id)

    return DocumentUploadResponse(
        doc_id=doc_id,
        status="queued",
        file_name=file_name,
        file_size_bytes=file_size,
    )


@router.get("/{doc_id}", response_model=DocumentDetailResponse)
async def get_document(
    doc_id: str,
    tenant_id: str = Depends(enforce_rate_limit),
):
    """Retrieve document metadata and extraction status.

    Generates a fresh presigned S3 download URL (1 hour TTL) on each call.
    Once extraction_status is 'indexed', the document is searchable.
    """
    pool = await get_db()
    doc = await db_svc.get_file_document(pool, tenant_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    download_url = None
    if doc.get("s3_key"):
        try:
            download_url = await storage_svc.generate_presigned_url(doc["s3_key"])
        except Exception as e:
            log.warning("Failed to generate presigned URL for %s: %s", doc_id, e)

    return DocumentDetailResponse(
        id=doc["id"],
        tenant_id=doc["tenant_id"],
        file_name=doc["file_name"],
        mime_type=doc["mime_type"],
        s3_key=doc["s3_key"],
        file_size_bytes=doc["file_size_bytes"] or 0,
        extraction_status=doc["extraction_status"],
        extraction_error=doc["extraction_error"],
        page_count=doc["page_count"],
        word_count=doc["word_count"],
        title=doc["title"],
        metadata=doc["metadata"] or {},
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        download_url=download_url,
    )


@router.get("/{doc_id}/download")
async def download_document(
    doc_id: str,
    tenant_id: str = Depends(enforce_rate_limit),
):
    """Redirect to a fresh presigned S3 download URL (302)."""
    pool = await get_db()
    doc = await db_svc.get_file_document(pool, tenant_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.get("s3_key"):
        raise HTTPException(status_code=404, detail="File not available for download.")

    try:
        url = await storage_svc.generate_presigned_url(doc["s3_key"])
    except Exception as e:
        log.error("Presigned URL generation failed for %s: %s", doc_id, e)
        raise HTTPException(status_code=500, detail="Could not generate download URL.")

    return RedirectResponse(url=url, status_code=302)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str,
    tenant_id: str = Depends(enforce_rate_limit),
):
    """Soft-delete document, remove ES chunks, enqueue S3 cleanup."""
    pool = await get_db()
    doc = await db_svc.get_file_document(pool, tenant_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Soft-delete in DB
    deleted = await db_svc.soft_delete_document(pool, tenant_id, doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove all ES chunks for this document
    es = get_es()
    try:
        await search_svc.delete_document_chunks(es, doc_id)
    except Exception as e:
        log.warning("Failed to delete ES chunks for doc %s: %s", doc_id, e)

    # Enqueue S3 cleanup (original file + extracted.txt)
    if doc.get("s3_key"):
        extracted_key = doc["s3_key"].rsplit("/", 1)[0] + "/extracted.txt"
        await publish_index_event({
            "action": "delete_s3",
            "doc_id": doc_id,
            "tenant_id": tenant_id,
            "s3_key": doc["s3_key"],
            "extracted_key": extracted_key,
        })

    redis = get_redis()
    await invalidate_tenant_cache(redis, tenant_id)

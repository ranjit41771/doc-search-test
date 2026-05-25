"""S3-compatible object storage service using boto3.

Configured for LocalStack in development (S3_ENDPOINT_URL=http://localstack:4566).
In production, remove S3_ENDPOINT_URL to use real AWS S3.

Note on presigned URLs with LocalStack:
  boto3 generates presigned URLs pointing to the configured endpoint_url host,
  i.e. http://localstack:4566/...  (docker-compose internal hostname).
  This works when clients run inside the same docker-compose network.
  For production (real S3), presigned URLs point to s3.amazonaws.com and work
  from anywhere. Document this distinction clearly for operators.
"""

import asyncio
import io
import logging
from functools import partial

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings

log = logging.getLogger(__name__)

_s3_client = None


def _get_client():
    global _s3_client
    if _s3_client is None:
        kwargs = {
            "region_name": settings.aws_region,
            "aws_access_key_id": settings.aws_access_key_id,
            "aws_secret_access_key": settings.aws_secret_access_key,
            # signature_version=s3v4 required for presigned URLs with LocalStack
            "config": Config(signature_version="s3v4"),
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def _ensure_bucket() -> None:
    client = _get_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            client.create_bucket(Bucket=settings.s3_bucket)
            log.info("Created S3 bucket: %s", settings.s3_bucket)
        else:
            raise


async def ensure_bucket() -> None:
    """Create the S3 bucket if it does not exist. Safe to call on every startup."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _ensure_bucket)


def _upload_file_sync(file_bytes: bytes, s3_key: str, content_type: str = "application/octet-stream") -> None:
    client = _get_client()
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=s3_key,
        Body=file_bytes,
        ContentType=content_type,
    )


async def upload_file(file_bytes: bytes, s3_key: str, content_type: str = "application/octet-stream") -> None:
    """Upload raw bytes to S3 at the given key."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_upload_file_sync, file_bytes, s3_key, content_type))


async def upload_text(text: str, s3_key: str) -> None:
    """Upload UTF-8 text to S3 (e.g. extracted.txt cache)."""
    await upload_file(text.encode("utf-8"), s3_key, content_type="text/plain; charset=utf-8")


def _download_file_sync(s3_key: str) -> bytes:
    client = _get_client()
    response = client.get_object(Bucket=settings.s3_bucket, Key=s3_key)
    return response["Body"].read()


async def download_file(s3_key: str) -> bytes:
    """Download an S3 object and return its bytes."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_download_file_sync, s3_key))


def _generate_presigned_url_sync(s3_key: str, expiry: int) -> str:
    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": s3_key},
        ExpiresIn=expiry,
    )


async def generate_presigned_url(s3_key: str, expiry: int = None) -> str:
    """Generate a time-limited presigned download URL for an S3 object.

    Default expiry is PRESIGNED_URL_EXPIRY (1 hour).
    LocalStack URLs use http://localstack:4566 — works inside docker-compose network.
    Replace with real S3 for public URLs in production.
    """
    if expiry is None:
        expiry = settings.presigned_url_expiry
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_generate_presigned_url_sync, s3_key, expiry))


def _delete_file_sync(s3_key: str) -> None:
    client = _get_client()
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=s3_key)
    except ClientError as e:
        log.warning("S3 delete failed for key %s: %s", s3_key, e)


async def delete_file(s3_key: str) -> None:
    """Delete an object from S3. Silently ignores missing keys."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_delete_file_sync, s3_key))

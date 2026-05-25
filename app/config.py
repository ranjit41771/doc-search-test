import logging
import warnings

from pydantic_settings import BaseSettings

_DEFAULT_JWT_SECRET = "change-me-in-production-use-a-long-random-secret"


class Settings(BaseSettings):
    elasticsearch_url: str = "http://localhost:9200"
    redis_url: str = "redis://localhost:6379"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    database_url: str = "postgresql://root@localhost:26257/defaultdb?sslmode=disable"

    es_index: str = "documents"
    es_index_shards: int = 5
    es_index_replicas: int = 1

    cache_ttl_seconds: int = 60
    rate_limit_requests: int = 6000       # 6000 req / 60s window = 100 req/s
    rate_limit_window_seconds: int = 60

    # Stricter limits for auth endpoints (per email, per window)
    auth_rate_limit_requests: int = 10
    auth_rate_limit_window_seconds: int = 60

    rabbitmq_index_queue: str = "document_index"

    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24

    # Document field size limits
    content_max_length: int = 1_000_000   # 1 MB
    metadata_max_bytes: int = 10_000      # 10 KB

    # S3 / LocalStack storage
    s3_endpoint_url: str = "http://localhost:4566"
    s3_bucket: str = "document-search"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    aws_region: str = "us-east-1"

    # File processing limits
    max_file_size_mb: int = 50
    chunk_size: int = 500
    chunk_overlap: int = 100
    presigned_url_expiry: int = 3600  # seconds (1 hour)

    model_config = {"env_file": ".env"}


settings = Settings()

if settings.jwt_secret == _DEFAULT_JWT_SECRET:
    warnings.warn(
        "JWT_SECRET is set to the insecure default value. "
        "Set the JWT_SECRET environment variable to a long random string before deploying to production.",
        stacklevel=1,
    )
    logging.getLogger(__name__).warning(
        "SECURITY: JWT_SECRET is using the insecure default. Set JWT_SECRET env var for production."
    )

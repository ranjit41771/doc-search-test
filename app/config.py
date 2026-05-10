from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    elasticsearch_url: str = "http://localhost:9200"
    redis_url: str = "redis://localhost:6379"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    database_url: str = "postgresql://root@localhost:26257/defaultdb?sslmode=disable"

    es_index: str = "documents"
    es_index_shards: int = 5
    es_index_replicas: int = 1

    cache_ttl_seconds: int = 60
    rate_limit_requests: int = 6000   # 6000 req / 60s window = 100 req/s
    rate_limit_window_seconds: int = 60

    rabbitmq_index_queue: str = "document_index"

    jwt_secret: str = "change-me-in-production-use-a-long-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24

    model_config = {"env_file": ".env"}


settings = Settings()

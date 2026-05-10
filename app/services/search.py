from datetime import datetime, timezone

from elasticsearch import AsyncElasticsearch, NotFoundError
import uuid

from app.config import settings
from app.models import DocumentCreate, DocumentResponse, SearchResponse, SearchResult

INDEX_MAPPING = {
    "settings": {
        "number_of_shards": settings.es_index_shards,
        "number_of_replicas": settings.es_index_replicas,
        "analysis": {
            "analyzer": {
                "content_analyzer": {
                    "type": "standard",
                    "stopwords": "_english_",
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "tenant_id": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "content_analyzer",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "content": {"type": "text", "analyzer": "content_analyzer"},
            "metadata": {"type": "object", "dynamic": False},
            "created_at": {"type": "date"},
            "updated_at": {"type": "date"},
            "deleted": {"type": "boolean"},
        }
    },
}


async def ensure_index(es: AsyncElasticsearch) -> None:
    exists = await es.indices.exists(index=settings.es_index)
    if not exists:
        await es.indices.create(index=settings.es_index, body=INDEX_MAPPING)


async def index_document(
    es: AsyncElasticsearch,
    tenant_id: str,
    doc: DocumentCreate,
) -> DocumentResponse:
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    body = {
        "tenant_id": tenant_id,
        "title": doc.title,
        "content": doc.content,
        "metadata": doc.metadata,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "deleted": False,
    }
    await es.index(index=settings.es_index, id=doc_id, body=body, refresh="wait_for")
    return DocumentResponse(id=doc_id, **body)


async def get_document(
    es: AsyncElasticsearch,
    tenant_id: str,
    doc_id: str,
) -> DocumentResponse:
    try:
        result = await es.get(index=settings.es_index, id=doc_id)
    except NotFoundError:
        return None

    src = result["_source"]
    # Enforce tenant isolation — never return another tenant's document
    if src["tenant_id"] != tenant_id or src.get("deleted"):
        return None

    return DocumentResponse(id=result["_id"], **src)


async def delete_document(
    es: AsyncElasticsearch,
    tenant_id: str,
    doc_id: str,
) -> bool:
    """Soft-delete: set deleted=True rather than removing from index."""
    try:
        result = await es.get(index=settings.es_index, id=doc_id)
    except NotFoundError:
        return False

    src = result["_source"]
    if src["tenant_id"] != tenant_id:
        return False  # tenant cannot delete another tenant's document

    now = datetime.now(timezone.utc)
    await es.update(
        index=settings.es_index,
        id=doc_id,
        body={"doc": {"deleted": True, "updated_at": now.isoformat()}},
        refresh="wait_for",
    )
    return True


async def search_documents(
    es: AsyncElasticsearch,
    tenant_id: str,
    query: str,
    page: int = 1,
    size: int = 10,
) -> tuple[list[SearchResult], int, int]:
    """Full-text search with mandatory tenant isolation filter.

    The tenant_id filter is in `filter` context (not `query`) so:
    - It does NOT affect the relevance score
    - ES caches filter results at the shard level (BitSet cache)
    - Results are only ever from the requesting tenant
    """
    from_offset = (page - 1) * size
    body = {
        "query": {
            "bool": {
                "must": {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^3", "content"],
                        "type": "best_fields",
                        "fuzziness": "AUTO",
                    }
                },
                # Filter context: cached, zero relevance impact, tenant-enforced
                "filter": [
                    {"term": {"tenant_id": tenant_id}},
                    {"term": {"deleted": False}},
                ],
            }
        },
        "highlight": {
            "fields": {
                "title": {"number_of_fragments": 1},
                "content": {"number_of_fragments": 3, "fragment_size": 150},
            }
        },
        "from": from_offset,
        "size": size,
    }

    # preference="_local" routes to the same shard replica on repeat calls,
    # improving ES shard-level query cache hit rate under load.
    response = await es.search(index=settings.es_index, body=body, preference="_local")
    took_ms = response["took"]
    total = response["hits"]["total"]["value"]

    results = []
    for hit in response["hits"]["hits"]:
        src = hit["_source"]
        highlights = {
            field: frags
            for field, frags in (hit.get("highlight") or {}).items()
        }
        results.append(
            SearchResult(
                id=hit["_id"],
                tenant_id=src["tenant_id"],
                title=src["title"],
                content=src["content"],
                metadata=src.get("metadata", {}),
                score=hit["_score"],
                highlights=highlights,
            )
        )

    return results, total, took_ms

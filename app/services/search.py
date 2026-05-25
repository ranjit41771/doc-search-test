"""Elasticsearch service — chunk-based document indexing and search.

Index structure (one ES doc per chunk):
  doc_id       : keyword  — the document ID in CockroachDB
  chunk_index  : integer  — position within the document
  page_number  : integer  — source page (PDF/PPTX page, DOCX=1, sheet number)
  tenant_id    : keyword  — mandatory tenant isolation filter
  file_name    : keyword  — original filename
  text         : text     — extracted/OCR'd chunk text (full-text searchable)
  s3_key       : keyword  — S3 object key for direct download

Search uses `collapse` on `doc_id` to return one result per document
(the best-scoring chunk) rather than N individual chunk hits.
"""

from elasticsearch import AsyncElasticsearch, NotFoundError

from app.config import settings

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
            "doc_id":       {"type": "keyword"},
            "chunk_index":  {"type": "integer"},
            "page_number":  {"type": "integer"},
            "tenant_id":    {"type": "keyword"},
            "file_name":    {"type": "keyword"},
            "text":         {"type": "text", "analyzer": "content_analyzer"},
            "s3_key":       {"type": "keyword"},
        }
    },
}


async def ensure_index(es: AsyncElasticsearch) -> None:
    """Create the ES index with the file-chunk mapping if it does not exist.

    NOTE: If upgrading from the previous text-payload mapping, you must delete
    the existing index first:  DELETE /documents  — then restart to recreate it.
    """
    exists = await es.indices.exists(index=settings.es_index)
    if not exists:
        await es.indices.create(index=settings.es_index, body=INDEX_MAPPING)


async def index_document_chunks(
    es: AsyncElasticsearch,
    doc_id: str,
    tenant_id: str,
    file_name: str,
    s3_key: str,
    chunks: list[dict],
) -> None:
    """Bulk-index a list of text chunks for a document.

    Each chunk dict must have: chunk_index, page_number, text.
    ES document IDs are formatted as  {doc_id}_{chunk_index}  to allow safe
    re-indexing (idempotent) and targeted per-doc deletion.
    """
    if not chunks:
        return

    operations = []
    for chunk in chunks:
        es_id = f"{doc_id}_{chunk['chunk_index']}"
        operations.append({"index": {"_index": settings.es_index, "_id": es_id}})
        operations.append({
            "doc_id":      doc_id,
            "chunk_index": chunk["chunk_index"],
            "page_number": chunk["page_number"],
            "tenant_id":   tenant_id,
            "file_name":   file_name,
            "text":        chunk["text"],
            "s3_key":      s3_key,
        })

    await es.bulk(body=operations, refresh="wait_for")


async def delete_document_chunks(
    es: AsyncElasticsearch,
    doc_id: str,
) -> None:
    """Delete all ES chunks belonging to a document (delete_by_query)."""
    try:
        await es.delete_by_query(
            index=settings.es_index,
            body={"query": {"term": {"doc_id": doc_id}}},
            refresh=True,
        )
    except NotFoundError:
        pass


async def search_documents(
    es: AsyncElasticsearch,
    tenant_id: str,
    query: str,
    page: int = 1,
    size: int = 10,
) -> tuple[list[dict], int, int]:
    """Full-text search across file chunks with tenant isolation.

    Uses `collapse` on `doc_id` to return one result per document —
    the chunk with the highest BM25 score for the query.

    Returns:
        (results, total_docs, took_ms)

    Each result dict:
        { doc_id, file_name, snippet, page_number, score, s3_key }
    """
    from_offset = (page - 1) * size
    body = {
        "query": {
            "bool": {
                "must": {
                    "match": {
                        "text": {
                            "query": query,
                            "fuzziness": "AUTO",
                        }
                    }
                },
                "filter": [
                    {"term": {"tenant_id": tenant_id}},
                ],
            }
        },
        "highlight": {
            "fields": {
                "text": {"number_of_fragments": 1, "fragment_size": 200}
            },
            "pre_tags": ["<em>"],
            "post_tags": ["</em>"],
        },
        # Collapse on doc_id → one result per document (best chunk wins)
        "collapse": {
            "field": "doc_id",
            "inner_hits": {
                "name": "best_chunk",
                "size": 1,
                "_source": ["chunk_index", "page_number", "text"],
            },
        },
        "from": from_offset,
        "size": size,
    }

    response = await es.search(index=settings.es_index, body=body, preference="_local")
    took_ms = response["took"]

    # Total is count of unique doc_ids (collapsed), not raw chunk hits
    total = response["hits"]["total"]["value"]

    results = []
    for hit in response["hits"]["hits"]:
        src = hit["_source"]

        # Best highlighted snippet
        highlights = hit.get("highlight", {})
        snippet_list = highlights.get("text", [])
        snippet = snippet_list[0] if snippet_list else src.get("text", "")[:200]

        results.append({
            "doc_id":      src["doc_id"],
            "file_name":   src["file_name"],
            "snippet":     snippet,
            "page_number": src["page_number"],
            "score":       hit["_score"],
            "s3_key":      src["s3_key"],
        })

    return results, total, took_ms

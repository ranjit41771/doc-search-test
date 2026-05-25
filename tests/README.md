# E2E Test Suite

End-to-end integration tests for the Distributed Document Search service.

## Quick Start

```bash
# Run with all defaults (localhost:8000, 300 req/s load test for 10s)
python3 tests/e2e_test.py

# Custom target and load parameters
python3 tests/e2e_test.py --base-url http://host:8000
python3 tests/e2e_test.py --load-rps 100 --load-duration 5
```

## Prerequisites

```bash
pip install requests httpx
docker compose up -d
```

The `docker compose up -d` command starts all required services:

| Service        | Role                                          |
|----------------|-----------------------------------------------|
| FastAPI app    | API server (port 8000)                        |
| CockroachDB    | Metadata storage (doc status, tenant records) |
| Elasticsearch  | Full-text search over extracted text chunks   |
| Redis          | L2 search result cache + rate limiting        |
| LocalStack     | S3-compatible object storage (file uploads)   |
| RabbitMQ       | Extraction job queue (SQS-compatible)         |

## Fixture Files

The test suite uses files in `tests/fixtures/` for upload testing:

| File                | Description                                                       |
|---------------------|-------------------------------------------------------------------|
| `sample.txt`        | ~500-word plain text document covering distributed systems topics |

The text file is automatically loaded at test start. If the file is missing the test falls back to an inline string so tests can still run.

A **minimal valid PDF** is embedded directly in the test file as a Python bytes constant (`MINIMAL_PDF`, ~800 bytes). This avoids any external PDF generation dependency and is uploaded as the third test document.

## Test Sections

| # | Section                         | Description                                                             |
|---|---------------------------------|-------------------------------------------------------------------------|
| 1 | Health Check                    | Tries `GET /ping` (public), falls back to `GET /health`                |
| 2 | Register                        | Creates two tenant accounts (Alpha + Beta); duplicate/weak-pw checks   |
| 3 | Login                           | Logs both tenants in, captures JWT tokens                               |
| 4 | GET /auth/me                    | Verifies JWT, checks no-token → 403                                    |
| 5 | POST /documents (file upload)   | Uploads 3 files via `multipart/form-data`; checks error cases          |
| 6 | Extraction Status Polling       | Polls `GET /documents/{id}` every 2s until `indexed` or `failed`       |
| 7 | Download Redirect               | Verifies `GET /documents/{id}/download` returns 302 + Location header  |
| 8 | GET /search                     | Full-text search; checks caching, isolation, pagination                |
| 9 | DELETE /documents/{id}          | Cross-tenant isolation, owner delete, double-delete, post-delete search |
|10 | Input Validation                | Missing file, empty file, empty/missing `q` parameter                  |
|11 | Load Test                       | 300 req/s × 10s across 50 tenants; p50/p90/p95/p99 latency report     |

## File Upload Tests (Section 5)

Files are uploaded using `requests` multipart form-data:

```python
import io
r = requests.post(
    f"{base}/documents",
    files={"file": ("report.txt", io.BytesIO(content_bytes), "text/plain")},
    data={"title": "My Document"},
    headers={"Authorization": f"Bearer {token}"},
)
```

Three uploads are performed:
1. **`distributed_systems_report.txt`** — full fixture file from `tests/fixtures/sample.txt`
2. **`raft_notes.txt`** — inline TXT bytes with Raft/CAP/Redis content
3. **`architecture.pdf`** — minimal valid PDF bytes embedded in the test

Error cases checked:
- No auth token → **403**
- Binary data with no valid magic bytes → **415** (unsupported MIME type)
- No file field in form → **422**
- Empty file (0 bytes) → **400 or 422**

## Extraction Polling (Section 6)

After upload, documents begin in `status: "queued"`. An async worker picks up the
extraction job from RabbitMQ, reads the file from S3, extracts text, and indexes
chunks into Elasticsearch. The polling loop checks status every 2 seconds for up to
30 seconds:

```
Waiting for extraction... attempt 1/15 (status: queued)
Waiting for extraction... attempt 2/15 (status: extracting)
Waiting for extraction... attempt 3/15 (status: indexed)
```

If extraction fails the test logs `extraction_error` and marks the assertion as failed.

## Download Redirect (Section 7)

`GET /documents/{id}/download` returns a 302 redirect to a presigned S3 URL (1-hour TTL).
The test uses `allow_redirects=False` in requests to capture the 302 directly:

```python
r = requests.get(url, allow_redirects=False, headers=auth_headers)
assert r.status_code == 302
assert r.headers["Location"].startswith("http")
```

## Search Response Shape

```json
{
  "results": [
    {
      "doc_id": "uuid",
      "file_name": "report.txt",
      "snippet": "...highlighted text...",
      "page_hint": 1,
      "score": 1.23,
      "download_url": "https://s3.../presigned",
      "extraction_status": "indexed"
    }
  ],
  "total": 3,
  "query_time_ms": 12,
  "query": "distributed systems",
  "tenant_id": "e2e-alpha",
  "cached": false
}
```

The second identical call returns `"cached": true` (Redis L2 cache, 60s TTL).

## Load Test

The load test targets `GET /search?q=distributed+systems` at 300 req/s across 50
tenant accounts (6 req/s per tenant, well within the 1,000 req/min enterprise limit).

Output includes a p50/p90/p95/p99 latency table and status-code breakdown. The
500ms SLA threshold is highlighted for each percentile.

## Latency Report

A full latency report is printed at the end of the run, covering every endpoint
called during the functional test sections plus the aggregate load test results.

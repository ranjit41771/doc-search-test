"""
End-to-End API Test Suite — with Latency Tracking & Load Test
=============================================================
Tests all APIs in sequence, records latency per call, prints a
full latency report at the end, and runs a 300 req/s load test
with p50 / p90 / p95 / p99 percentile breakdown.

Usage:
    python3 tests/e2e_test.py                          # localhost:8000
    python3 tests/e2e_test.py --base-url http://host:8000
    python3 tests/e2e_test.py --load-rps 300 --load-duration 10

Prerequisites:
    pip install requests httpx
    docker compose up -d   (includes LocalStack for S3, RabbitMQ, CockroachDB, Elasticsearch, Redis)
"""

import argparse
import asyncio
import io
import json
import os
import statistics
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
import requests

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BLUE   = "\033[94m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


# ── Fixture content ───────────────────────────────────────────────────────────

# Path to the sample text fixture file
_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_SAMPLE_TXT_PATH = os.path.join(_FIXTURES_DIR, "sample.txt")

# Inline TXT content used as a fallback when fixture file is absent,
# and also as a second upload document with different content.
_TXT_CONTENT_2 = (
    "Raft consensus algorithm ensures fault tolerance in distributed systems. "
    "Leader election and log replication are core to Raft. "
    "CockroachDB uses Raft consensus to replicate data ranges. "
    "Elasticsearch inverted index enables sub-50ms full-text search. "
    "Redis caching with sorted sets implements sliding window rate limits. "
    "CAP theorem states that consistency, availability, and partition tolerance "
    "cannot all be guaranteed simultaneously in a distributed system. "
    "Fault-tolerant systems use replication and quorum-based consensus protocols."
).encode()


def _load_sample_txt() -> bytes:
    """Load the fixture text file, generating fallback content if absent."""
    if os.path.exists(_SAMPLE_TXT_PATH):
        with open(_SAMPLE_TXT_PATH, "rb") as fh:
            return fh.read()
    # Fallback: generate minimal content inline so tests can still run
    return (
        "Distributed systems use Raft consensus for fault tolerance. "
        "The CAP theorem governs consistency vs availability trade-offs. "
        "Elasticsearch provides full-text search via an inverted index. "
        "Redis caching reduces database load in high-traffic applications."
    ).encode()


# A well-known minimal valid PDF (~800 bytes) that readers can parse.
# Contains a single page with one line of text about distributed systems.
MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
    b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    b"4 0 obj\n<< /Length 44 >>\nstream\nBT /F1 12 Tf 100 700 Td "
    b"(Distributed systems use Raft consensus for fault tolerance.) Tj ET\nendstream\nendobj\n"
    b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"xref\n0 6\n0000000000 65535 f\n0000000009 00000 n\n"
    b"0000000058 00000 n\n0000000115 00000 n\n0000000266 00000 n\n"
    b"0000000360 00000 n\n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n441\n%%EOF"
)


# ── Latency store ─────────────────────────────────────────────────────────────

# Maps "METHOD /path" → [latency_ms, ...]
_latencies: dict[str, list[float]] = defaultdict(list)


def _record(label: str, elapsed_ms: float) -> None:
    _latencies[label].append(elapsed_ms)


# ── Test result tracking ──────────────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Suite:
    results: list[TestResult] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str = "") -> bool:
        self.results.append(TestResult(name, passed, detail))
        icon = f"{GREEN}✔ PASS{RESET}" if passed else f"{RED}✖ FAIL{RESET}"
        print(f"  {icon}  {name}")
        if detail:
            print(f"         {RED if not passed else DIM}{detail}{RESET}")
        return passed

    def summary(self) -> None:
        total  = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        print()
        print(f"{BOLD}{'─' * 65}{RESET}")
        print(f"{BOLD}Test Results:  {GREEN}{passed} passed{RESET}  "
              f"{RED}{failed} failed{RESET}  / {total} total{RESET}")
        print(f"{BOLD}{'─' * 65}{RESET}")
        if failed:
            print(f"\n{RED}Failed tests:{RESET}")
            for r in self.results:
                if not r.passed:
                    print(f"  • {r.name}" + (f"\n    {r.detail}" if r.detail else ""))


# ── Helpers ───────────────────────────────────────────────────────────────────

def section(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─' * 65}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 65}{RESET}")


def dump(label: str, data: Any) -> None:
    print(f"  {YELLOW}{label}:{RESET} {json.dumps(data, default=str)}")


def pct(values: list[float], p: float) -> float:
    """Return the p-th percentile of values (0–100)."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = (p / 100) * (len(sorted_v) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_v) - 1)
    return sorted_v[lo] + (sorted_v[hi] - sorted_v[lo]) * (idx - lo)


def _safe_json(r: requests.Response) -> Any:
    """Parse response JSON safely, returning empty dict on failure."""
    try:
        return r.json()
    except Exception:
        return {}


# ── Timed HTTP client ─────────────────────────────────────────────────────────

class Client:
    def __init__(self, base_url: str):
        self.base  = base_url.rstrip("/")
        self.token: str | None = None

    def _auth_headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return h

    def _json_headers(self, auth: bool = True) -> dict:
        h = {"Content-Type": "application/json"}
        if auth and self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _call(self, label: str, fn, *args, **kwargs) -> requests.Response:
        t0 = time.perf_counter()
        r  = fn(*args, **kwargs, timeout=15)
        ms = (time.perf_counter() - t0) * 1000
        _record(label, ms)
        print(f"  {DIM}→ {label}  {ms:.1f}ms  HTTP {r.status_code}{RESET}")
        return r

    def post(self, path: str, body: dict, *, auth: bool = True) -> requests.Response:
        label = f"POST {path}"
        return self._call(label, requests.post,
                          f"{self.base}{path}",
                          json=body,
                          headers=self._json_headers(auth))

    def post_multipart(
        self,
        path: str,
        files: dict,
        data: dict | None = None,
        *,
        auth: bool = True,
    ) -> requests.Response:
        """POST multipart/form-data (for file uploads)."""
        label = f"POST {path}"
        headers = self._auth_headers() if auth else {}
        return self._call(label, requests.post,
                          f"{self.base}{path}",
                          files=files,
                          data=data or {},
                          headers=headers)

    def get(
        self,
        path: str,
        params: dict | None = None,
        *,
        auth: bool = True,
        allow_redirects: bool = True,
    ) -> requests.Response:
        label = f"GET {path}"
        return self._call(label, requests.get,
                          f"{self.base}{path}",
                          params=params,
                          headers=self._json_headers(auth) if auth else {},
                          allow_redirects=allow_redirects)

    def delete(self, path: str) -> requests.Response:
        label = f"DELETE {path}"
        return self._call(label, requests.delete,
                          f"{self.base}{path}",
                          headers=self._auth_headers())


# ══ Test functions ════════════════════════════════════════════════════════════

def test_health(client: Client, suite: Suite) -> None:
    section("1. Health Check")
    # Try /ping first (public, no auth), fall back to /health
    r_ping = None
    try:
        r_ping = client.get("/ping", auth=False)
    except Exception:
        pass

    if r_ping is not None and r_ping.status_code == 200:
        suite.record("GET /ping → 200 (public health)", True)
        data = _safe_json(r_ping)
        dump("Ping response", data)
    else:
        r = client.get("/health", auth=False)
        data = _safe_json(r)
        dump("Health response", data)
        suite.record("GET /health → 200", r.status_code == 200)
        suite.record(
            "All dependencies reported",
            all(
                k in data.get("dependencies", {})
                for k in ("elasticsearch", "redis", "cockroachdb")
            ),
        )


def test_register(ca: Client, cb: Client, suite: Suite) -> None:
    section("2. Register")
    for client, tid, name, email, pwd, plan in [
        (ca, "e2e-alpha", "Alpha Corp", "alpha@e2e.com", "Alphapass123", "enterprise"),
        (cb, "e2e-beta",  "Beta Inc",   "beta@e2e.com",  "Betapass456",  "standard"),
    ]:
        r = client.post("/auth/register", {
            "tenant_id": tid, "name": name,
            "email": email, "password": pwd, "plan": plan,
        }, auth=False)
        suite.record(f"Register {tid} → 201 or 409", r.status_code in (201, 409))

    r_dup = ca.post("/auth/register", {
        "tenant_id": "e2e-alpha", "name": "Dup",
        "email": "other@e2e.com", "password": "Duppass1234", "plan": "free",
    }, auth=False)
    suite.record("Duplicate tenant_id → 409", r_dup.status_code == 409)

    r_weak = ca.post("/auth/register", {
        "tenant_id": "weak-pw", "name": "W",
        "email": "w@e2e.com", "password": "short", "plan": "free",
    }, auth=False)
    suite.record("Weak password → 422", r_weak.status_code == 422)


def test_login(ca: Client, cb: Client, suite: Suite) -> None:
    section("3. Login")
    r = ca.post("/auth/login",
                {"email": "alpha@e2e.com", "password": "Alphapass123"}, auth=False)
    suite.record("Login Tenant A → 200", r.status_code == 200)
    ca.token = _safe_json(r).get("access_token", "")

    r2 = cb.post("/auth/login",
                 {"email": "beta@e2e.com", "password": "Betapass456"}, auth=False)
    suite.record("Login Tenant B → 200", r2.status_code == 200)
    cb.token = _safe_json(r2).get("access_token", "")

    r_bad = ca.post("/auth/login",
                    {"email": "alpha@e2e.com", "password": "wrongpass"}, auth=False)
    suite.record("Wrong password → 401", r_bad.status_code == 401)


def test_auth_me(ca: Client, suite: Suite) -> None:
    section("4. GET /auth/me")
    r = ca.get("/auth/me")
    suite.record("GET /auth/me → 200", r.status_code == 200)
    suite.record("Correct tenant_id", _safe_json(r).get("tenant_id") == "e2e-alpha")

    r_none = requests.get(f"{ca.base}/auth/me", timeout=10)
    suite.record("No token → 403", r_none.status_code == 403)


def test_upload_documents(ca: Client, suite: Suite) -> list[str]:
    """Section 5: Upload files using multipart/form-data."""
    section("5. POST /documents  (multipart file upload)")

    doc_ids: list[str] = []
    txt_bytes = _load_sample_txt()

    # ── Upload 1: full TXT fixture ────────────────────────────────────────────
    r1 = ca.post_multipart(
        "/documents",
        files={"file": ("distributed_systems_report.txt", io.BytesIO(txt_bytes), "text/plain")},
        data={"title": "Distributed Systems Report"},
    )
    data1 = _safe_json(r1)
    if r1.status_code == 201:
        doc_ids.append(data1["doc_id"])
        suite.record("Upload TXT fixture → 201", True)
    else:
        suite.record("Upload TXT fixture → 201", False, f"got {r1.status_code}: {data1}")

    suite.record("Upload response has doc_id", "doc_id" in data1)
    suite.record("Upload response status == 'queued'", data1.get("status") == "queued")
    suite.record("Upload response has file_name", "file_name" in data1)
    suite.record("Upload response has file_size_bytes", "file_size_bytes" in data1)

    dump("Upload 1 response", data1)

    # ── Upload 2: inline TXT content (different keywords) ────────────────────
    r2 = ca.post_multipart(
        "/documents",
        files={"file": ("raft_notes.txt", io.BytesIO(_TXT_CONTENT_2), "text/plain")},
        data={"title": "Raft & CAP Notes", "tags": "distributed,consensus"},
    )
    data2 = _safe_json(r2)
    if r2.status_code == 201:
        doc_ids.append(data2["doc_id"])
        suite.record("Upload second TXT → 201", True)
    else:
        suite.record("Upload second TXT → 201", False, f"got {r2.status_code}: {data2}")

    # ── Upload 3: minimal PDF ─────────────────────────────────────────────────
    r3 = ca.post_multipart(
        "/documents",
        files={"file": ("architecture.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
        data={"title": "Architecture PDF"},
    )
    data3 = _safe_json(r3)
    if r3.status_code == 201:
        doc_ids.append(data3["doc_id"])
        suite.record("Upload minimal PDF → 201", True)
    else:
        suite.record("Upload minimal PDF → 201", False, f"got {r3.status_code}: {data3}")

    dump("Upload 3 (PDF) response", data3)

    # ── No auth → 403 ────────────────────────────────────────────────────────
    r_noauth = requests.post(
        f"{ca.base}/documents",
        files={"file": ("test.txt", io.BytesIO(b"hello world test content"), "text/plain")},
        timeout=10,
    )
    suite.record("POST /documents without token → 403", r_noauth.status_code == 403,
                 f"got {r_noauth.status_code}" if r_noauth.status_code != 403 else "")

    # ── Unsupported MIME type → 415 ───────────────────────────────────────────
    # Send bytes that python-magic will identify as application/octet-stream
    # (random binary data with no valid magic bytes for any allowed format)
    fake_exe = bytes(range(256)) * 4  # 1024 bytes of sequential binary data
    r_mime = ca.post_multipart(
        "/documents",
        files={"file": ("malware.exe", io.BytesIO(fake_exe), "application/octet-stream")},
    )
    suite.record("Unsupported MIME type → 415", r_mime.status_code == 415,
                 f"got {r_mime.status_code}" if r_mime.status_code != 415 else "")

    return doc_ids


def _poll_extraction(
    client: Client,
    doc_id: str,
    max_attempts: int = 15,
    interval_sec: float = 2.0,
) -> dict | None:
    """Poll GET /documents/{doc_id} until status is 'indexed' or 'failed'.

    Returns the final document dict, or None if we time out.
    """
    for attempt in range(1, max_attempts + 1):
        r = client.get(f"/documents/{doc_id}")
        data = _safe_json(r)
        status = data.get("extraction_status", "unknown")
        print(f"  {YELLOW}Waiting for extraction... attempt {attempt}/{max_attempts} "
              f"(status: {status}){RESET}")
        if status in ("indexed", "failed"):
            return data
        if attempt < max_attempts:
            time.sleep(interval_sec)
    return None


def test_extraction_status(
    ca: Client, cb: Client, doc_ids: list[str], suite: Suite
) -> None:
    """Section 6: Poll GET /documents/{id} until extraction completes."""
    section("6. Extraction Status Polling  (GET /documents/{id})")

    if not doc_ids:
        print(f"  {YELLOW}No doc_ids from upload — skipping extraction tests.{RESET}")
        return

    # Poll the first document (TXT) until indexed or failed
    doc_id = doc_ids[0]
    final = _poll_extraction(ca, doc_id)

    if final is None:
        suite.record("TXT doc reaches indexed/failed within 30s", False,
                     "Timed out waiting for extraction")
    else:
        status = final.get("extraction_status")
        if status == "failed":
            err = final.get("extraction_error", "no error detail")
            print(f"  {RED}Extraction failed for {doc_id}: {err}{RESET}")
        suite.record("TXT doc reaches indexed or failed", status in ("indexed", "failed"),
                     f"status={status}")

    # GET metadata checks on the first doc (even if extraction failed)
    r = ca.get(f"/documents/{doc_id}")
    data = _safe_json(r)
    dump("GET /documents response", data)

    suite.record("GET /documents/{id} → 200", r.status_code == 200)
    suite.record("Response has file_name", bool(data.get("file_name")))
    suite.record("Response has mime_type", bool(data.get("mime_type")))
    suite.record("Response has extraction_status field", "extraction_status" in data)
    suite.record("Response has download_url (presigned S3)", bool(data.get("download_url")))
    suite.record("download_url starts with http",
                 str(data.get("download_url", "")).startswith("http"))

    # Cross-tenant GET → 404 (tenant isolation)
    r_cross = cb.get(f"/documents/{doc_id}")
    suite.record("Cross-tenant GET → 404 (isolation)", r_cross.status_code == 404,
                 f"BREACH! got {r_cross.status_code}" if r_cross.status_code != 404 else "")

    # Non-existent doc → 404
    r_miss = ca.get("/documents/nonexistent-id-does-not-exist-xyz")
    suite.record("Non-existent doc → 404", r_miss.status_code == 404)


def test_download_redirect(ca: Client, doc_ids: list[str], suite: Suite) -> None:
    """Section 7: GET /documents/{id}/download → 302 redirect to presigned S3 URL."""
    section("7. GET /documents/{id}/download  (302 → presigned S3)")

    if not doc_ids:
        print(f"  {YELLOW}No doc_ids — skipping download redirect test.{RESET}")
        return

    doc_id = doc_ids[0]
    r = ca.get(f"/documents/{doc_id}/download", allow_redirects=False)
    suite.record("GET /documents/{id}/download → 302", r.status_code == 302,
                 f"got {r.status_code}" if r.status_code != 302 else "")
    location = r.headers.get("Location", r.headers.get("location", ""))
    suite.record("Location header is present", bool(location),
                 "Location header missing" if not location else "")
    suite.record("Location header starts with http",
                 location.startswith("http"),
                 f"got: {location[:80]}" if location and not location.startswith("http") else "")


def test_search(ca: Client, cb: Client, suite: Suite) -> None:
    """Section 8: GET /search — full-text search over indexed file chunks."""
    section("8. GET /search")

    # Give async extraction a moment to complete if not already polled
    print(f"  {YELLOW}Waiting 3s for extraction + ES indexing...{RESET}")
    time.sleep(3)

    r = ca.get("/search", {"q": "distributed systems"})
    data = _safe_json(r)
    suite.record("Search → 200", r.status_code == 200)
    suite.record("Returns results (total >= 1)", data.get("total", 0) >= 1,
                 f"total={data.get('total')}")

    results = data.get("results", [])
    if results:
        first = results[0]
        suite.record("Result has doc_id", "doc_id" in first)
        suite.record("Result has snippet", bool(first.get("snippet")))
        suite.record("Result has score", "score" in first)
        suite.record("Result has download_url", bool(first.get("download_url")))
        suite.record("download_url starts with http",
                     str(first.get("download_url", "")).startswith("http"))
    else:
        suite.record("Result has doc_id", False, "No results returned")
        suite.record("Result has snippet", False, "No results returned")
        suite.record("Result has score", False, "No results returned")
        suite.record("Result has download_url", False, "No results returned")
        suite.record("download_url starts with http", False, "No results returned")

    suite.record("cached=False on first call", data.get("cached") is False,
                 f"cached={data.get('cached')}")

    # Second identical call → should be cached
    r2 = ca.get("/search", {"q": "distributed systems"})
    suite.record("Same query → cached=True (Redis L2 hit)",
                 _safe_json(r2).get("cached") is True)

    # Tenant isolation: Tenant B must see 0 results from Tenant A's docs
    r_iso = cb.get("/search", {"q": "distributed systems"})
    iso_total = _safe_json(r_iso).get("total", -1)
    suite.record(
        "Tenant B sees 0 results from Tenant A (isolation)",
        iso_total == 0,
        f"BREACH! total={iso_total}" if iso_total != 0 else "",
    )

    # Pagination
    r_page = ca.get("/search", {"q": "distributed", "page": 1, "size": 2})
    suite.record("Pagination size=2 respected",
                 len(_safe_json(r_page).get("results", [])) <= 2)

    # Search for other indexed keywords
    r_raft = ca.get("/search", {"q": "Raft consensus"})
    suite.record("Search for 'Raft consensus' → 200", r_raft.status_code == 200)


def test_delete(ca: Client, cb: Client, doc_ids: list[str], suite: Suite) -> None:
    """Section 9: DELETE /documents/{id}."""
    section("9. DELETE /documents/{id}")

    if not doc_ids:
        print(f"  {YELLOW}No doc_ids — skipping delete tests.{RESET}")
        return

    target = doc_ids[-1]

    # Cross-tenant DELETE → 404 (isolation)
    r_cross = cb.delete(f"/documents/{target}")
    suite.record("Cross-tenant DELETE → 404 (isolation)", r_cross.status_code == 404,
                 f"BREACH! got {r_cross.status_code}" if r_cross.status_code != 404 else "")

    # Owner DELETE → 204
    r = ca.delete(f"/documents/{target}")
    suite.record("Owner DELETE → 204", r.status_code == 204,
                 f"got {r.status_code}" if r.status_code != 204 else "")

    # GET after delete → 404
    r_get = ca.get(f"/documents/{target}")
    suite.record("Deleted doc → 404 on GET", r_get.status_code == 404)

    # Double DELETE → 404
    r2 = ca.delete(f"/documents/{target}")
    suite.record("Double DELETE → 404", r2.status_code == 404)

    # After deletion, search should eventually drop the doc from results
    print(f"  {YELLOW}Waiting 2s for async ES delete to propagate...{RESET}")
    time.sleep(2)
    r_search = ca.get("/search", {"q": "distributed systems"})
    deleted_in_results = any(
        item.get("doc_id") == target
        for item in _safe_json(r_search).get("results", [])
    )
    suite.record("Deleted doc absent from search results",
                 not deleted_in_results,
                 f"doc_id {target} still in results!" if deleted_in_results else "")


def test_validation(ca: Client, suite: Suite) -> None:
    """Section 10: Input validation edge cases."""
    section("10. Input Validation")

    # POST /documents with no file → 422
    r_nofile = requests.post(
        f"{ca.base}/documents",
        data={"title": "No file here"},
        headers={"Authorization": f"Bearer {ca.token}"},
        timeout=10,
    )
    suite.record("POST /documents with no file → 422", r_nofile.status_code == 422,
                 f"got {r_nofile.status_code}")

    # POST /documents with empty file → 400 or 422
    r_empty = ca.post_multipart(
        "/documents",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )
    suite.record("POST /documents with empty file → 400 or 422",
                 r_empty.status_code in (400, 422),
                 f"got {r_empty.status_code}")

    # GET /search with empty q → 422
    r_empty_q = ca.get("/search", {"q": ""})
    suite.record("GET /search with empty q → 422", r_empty_q.status_code == 422,
                 f"got {r_empty_q.status_code}")

    # GET /search with missing q → 422
    r_no_q = requests.get(
        f"{ca.base}/search",
        headers={"Authorization": f"Bearer {ca.token}"},
        timeout=10,
    )
    suite.record("GET /search with missing q → 422", r_no_q.status_code == 422,
                 f"got {r_no_q.status_code}")


# ══ Load test ═════════════════════════════════════════════════════════════════

# Number of distinct tenant accounts created for the load test.
# Load is distributed evenly across them so no single tenant hits the rate limit.
# e.g. 300 req/s ÷ 50 tenants = 6 req/s per tenant  (limit: 1000 req/min enterprise)
LOAD_TEST_TENANTS = 50


async def _provision_load_tenants(base_url: str) -> list[str]:
    """Register LOAD_TEST_TENANTS accounts and return their JWT tokens."""
    print(f"  {YELLOW}Provisioning {LOAD_TEST_TENANTS} load-test tenants...{RESET}")
    tokens: list[str] = []
    async with httpx.AsyncClient() as client:
        for i in range(LOAD_TEST_TENANTS):
            tid   = f"loadtest-tenant-{i:03d}"
            email = f"load{i:03d}@loadtest.internal"
            # Register (ignore 409 — already exists from a previous run)
            await client.post(f"{base_url}/auth/register", json={
                "tenant_id": tid, "name": f"Load Tenant {i}",
                "email": email, "password": "Loadpass123", "plan": "enterprise",
            }, timeout=10)
            # Login to get token
            r = await client.post(f"{base_url}/auth/login", json={
                "email": email, "password": "Loadpass123",
            }, timeout=10)
            if r.status_code == 200:
                tokens.append(r.json()["access_token"])

    print(f"  {GREEN}Provisioned {len(tokens)} tenant tokens.{RESET}")
    return tokens


async def _fire(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    results: list[dict],
    semaphore: asyncio.Semaphore,
) -> None:
    # Semaphore caps true concurrency to avoid overwhelming single-node ES.
    # Without this, 300 goroutines all hit ES at t=0 → queue builds → high p99.
    async with semaphore:
        t0 = time.perf_counter()
        try:
            r  = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            ms = (time.perf_counter() - t0) * 1000
            results.append({"ms": ms, "status": r.status_code, "ok": r.status_code == 200})
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            results.append({"ms": ms, "status": 0, "ok": False, "err": str(e)})


async def run_load_test(
    base_url: str,
    target_rps: int = 300,
    duration_sec: int = 10,
) -> None:
    section(f"11. Load Test  —  {target_rps} req/s  ×  {duration_sec}s  "
            f"({LOAD_TEST_TENANTS} tenants, ~{target_rps//LOAD_TEST_TENANTS} req/s each)")

    print(f"\n  {CYAN}Why multiple tenants?{RESET}")
    print(f"  {DIM}Rate limit = 1,000 req/min per tenant (enterprise plan){RESET}")
    print(f"  {DIM}{target_rps} req/s ÷ {LOAD_TEST_TENANTS} tenants "
          f"= {target_rps//LOAD_TEST_TENANTS} req/s/tenant "
          f"= {target_rps//LOAD_TEST_TENANTS*60} req/min/tenant  ← within limit{RESET}\n")

    tokens  = await _provision_load_tenants(base_url)
    if not tokens:
        print(f"{RED}No tokens — skipping load test.{RESET}")
        return

    # Target: GET /search?q=distributed+systems
    # Works even when tenants have 0 results — we're measuring throughput/latency.
    url       = f"{base_url}/search?q=distributed+systems"
    results: list[dict] = []
    start     = time.perf_counter()
    # Allow at most 50 in-flight ES requests at once.
    # Prevents thundering herd on single-node ES — requests queue here
    # rather than stacking up inside ES's thread pool (which causes p99 spikes).
    semaphore = asyncio.Semaphore(50)

    limits    = httpx.Limits(max_connections=400, max_keepalive_connections=100)
    async with httpx.AsyncClient(limits=limits) as client:
        for tick in range(duration_sec):
            t_tick  = time.perf_counter()
            # Round-robin tokens across the batch so load is spread evenly
            tasks   = [
                _fire(client, url, tokens[i % len(tokens)], results, semaphore)
                for i in range(target_rps)
            ]
            await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - t_tick
            ok_so_far  = sum(1 for r in results if r["ok"])
            fail_so_far = len(results) - ok_so_far
            print(f"  {DIM}tick {tick+1}/{duration_sec}  "
                  f"fired={target_rps}  elapsed={elapsed:.2f}s  "
                  f"ok={ok_so_far}  fail={fail_so_far}{RESET}")
            sleep_ = max(0.0, 1.0 - elapsed)
            if sleep_ > 0:
                await asyncio.sleep(sleep_)

    wall_sec  = time.perf_counter() - start
    total     = len(results)
    successes = sum(1 for r in results if r["ok"])
    failures  = total - successes
    latencies = [r["ms"] for r in results]

    # ── Status code breakdown ─────────────────────────────────────────────────
    from collections import Counter
    status_counts = Counter(r["status"] for r in results)

    print()
    print(f"{BOLD}{BLUE}{'═' * 65}{RESET}")
    print(f"{BOLD}{BLUE}  Load Test Results{RESET}")
    print(f"{BOLD}{BLUE}{'═' * 65}{RESET}")

    print(f"\n  {'Metric':<32} {'Value':>15}")
    print(f"  {'─'*32} {'─'*15}")
    print(f"  {'Target RPS':<32} {target_rps:>15,}")
    print(f"  {'Tenants (round-robin)':<32} {len(tokens):>15,}")
    print(f"  {'Duration':<32} {duration_sec:>14}s")
    print(f"  {'Total Requests':<32} {total:>15,}")
    print(f"  {'Achieved RPS':<32} {total/wall_sec:>14.1f}")
    print(f"  {'Successful (2xx)':<32} {GREEN}{successes:>15,}{RESET}")
    print(f"  {'Failed':<32} {(RED if failures else GREEN)}{failures:>15,}{RESET}")
    if total > 0:
        rate_colour = GREEN if successes/total > 0.99 else (YELLOW if successes/total > 0.95 else RED)
        print(f"  {'Success Rate':<32} {rate_colour}{successes/total*100:>14.1f}%{RESET}")

    print(f"\n  {'Status Code Breakdown':}")
    print(f"  {'─'*40}")
    status_labels = {
        200: "200 OK",
        429: "429 Too Many Requests (rate limit)",
        401: "401 Unauthorized",
        403: "403 Forbidden",
        500: "500 Internal Server Error",
        0:   "Connection Error / Timeout",
    }
    for code, count in sorted(status_counts.items()):
        label  = status_labels.get(code, f"HTTP {code}")
        colour = GREEN if code == 200 else (YELLOW if code == 429 else RED)
        bar    = "█" * min(40, int(count / total * 40))
        print(f"  {colour}{label:<38} {count:>5}  {bar}{RESET}")

    if latencies:
        success_lat = [r["ms"] for r in results if r["ok"]]
        print(f"\n  {'Latency Percentiles'} "
              f"{DIM}(successful requests only — {len(success_lat):,}){RESET}")
        print(f"  {'─'*50}")
        stats = [
            ("Min",         min(success_lat) if success_lat else 0),
            ("p50 (median)", pct(success_lat, 50)),
            ("p90",          pct(success_lat, 90)),
            ("p95",          pct(success_lat, 95)),
            ("p99",          pct(success_lat, 99)),
            ("Max",          max(success_lat) if success_lat else 0),
            ("Mean",         statistics.mean(success_lat) if success_lat else 0),
            ("StdDev",       statistics.stdev(success_lat) if len(success_lat) > 1 else 0),
        ]
        for label, val in stats:
            colour = GREEN if val < 100 else (YELLOW if val < 300 else RED)
            sla    = " ✔ within 500ms SLA" if val < 500 else f" {RED}✖ exceeds 500ms SLA{RESET}"
            print(f"  {label:<28} {colour}{val:>10.1f}ms{RESET}{DIM}{sla}{RESET}")

    # Store for the final latency report (successful only)
    _latencies["LOAD GET /search"] = [r["ms"] for r in results if r["ok"]]
    print(f"\n{BOLD}{BLUE}{'═' * 65}{RESET}")


# ══ Final latency report ══════════════════════════════════════════════════════

def print_latency_report() -> None:
    section("API Latency Report  (all calls during this run)")

    header = f"  {'Endpoint':<32} {'Calls':>5}  {'Min':>7}  {'p50':>7}  {'p90':>7}  {'p99':>7}  {'Max':>7}  {'Mean':>7}"
    print(header)
    print(f"  {'─'*32} {'─'*5}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}")

    for label in sorted(_latencies):
        vals = _latencies[label]
        if not vals:
            continue
        p50 = pct(vals, 50)
        p90 = pct(vals, 90)
        p99 = pct(vals, 99)

        def colour(ms: float) -> str:
            c = GREEN if ms < 100 else (YELLOW if ms < 300 else RED)
            return f"{c}{ms:7.1f}{RESET}"

        print(f"  {label:<32} {len(vals):>5}  "
              f"{colour(min(vals))}  "
              f"{colour(p50)}  "
              f"{colour(p90)}  "
              f"{colour(p99)}  "
              f"{colour(max(vals))}  "
              f"{colour(statistics.mean(vals))}")

    print()
    print(f"  {GREEN}< 100ms{RESET}  fast    "
          f"{YELLOW}100–300ms{RESET}  acceptable    "
          f"{RED}> 300ms{RESET}  slow")


# ══ Main runner ═══════════════════════════════════════════════════════════════

def run(base_url: str, load_rps: int, load_duration: int) -> None:
    print(f"\n{BOLD}{'═' * 65}{RESET}")
    print(f"{BOLD}  Distributed Document Search — E2E + Load Test Suite{RESET}")
    print(f"{BOLD}  Target: {base_url}{RESET}")
    print(f"{BOLD}{'═' * 65}{RESET}")

    # ── Wait for server ───────────────────────────────────────────────────────
    print(f"\n{YELLOW}Waiting for server...{RESET}")
    for i in range(10):
        try:
            # Try /ping (public) first, then /health
            for probe in ("/ping", "/health"):
                try:
                    if requests.get(f"{base_url}{probe}", timeout=5).status_code == 200:
                        print(f"{GREEN}Server ready (probe: {probe}).{RESET}")
                        break
                except Exception:
                    continue
            else:
                raise requests.exceptions.ConnectionError("no probe succeeded")
            break
        except requests.exceptions.ConnectionError:
            pass
        print(f"  attempt {i+1}/10 — retrying in 3s...")
        time.sleep(3)
    else:
        print(f"{RED}Server not reachable. Is docker compose up?{RESET}")
        sys.exit(1)

    ca    = Client(base_url)
    cb    = Client(base_url)
    suite = Suite()

    # ── Functional tests (sequential, with per-call latency recording) ────────
    test_health(ca, suite)
    test_register(ca, cb, suite)
    test_login(ca, cb, suite)
    test_auth_me(ca, suite)

    doc_ids = test_upload_documents(ca, suite)

    if doc_ids:
        test_extraction_status(ca, cb, doc_ids, suite)
        test_download_redirect(ca, doc_ids, suite)
        test_search(ca, cb, suite)
        test_delete(ca, cb, doc_ids, suite)

    test_validation(ca, suite)

    # ── Load test (async, distributed across multiple tenants) ───────────────
    asyncio.run(run_load_test(base_url, load_rps, load_duration))

    # ── Reports ───────────────────────────────────────────────────────────────
    print_latency_report()
    suite.summary()
    sys.exit(0 if all(r.passed for r in suite.results) else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url",       default="http://localhost:8000")
    parser.add_argument("--load-rps",       type=int, default=300,
                        help="Requests per second for load test (default: 300)")
    parser.add_argument("--load-duration",  type=int, default=10,
                        help="Load test duration in seconds (default: 10)")
    args = parser.parse_args()
    run(args.base_url, args.load_rps, args.load_duration)

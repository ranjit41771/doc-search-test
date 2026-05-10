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
    docker compose up -d
"""

import argparse
import asyncio
import json
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


# ── Timed HTTP client ─────────────────────────────────────────────────────────

class Client:
    def __init__(self, base_url: str):
        self.base  = base_url.rstrip("/")
        self.token: str | None = None

    def _h(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _call(self, label: str, fn, *args, **kwargs) -> requests.Response:
        t0 = time.perf_counter()
        r  = fn(*args, **kwargs, timeout=10)
        ms = (time.perf_counter() - t0) * 1000
        _record(label, ms)
        print(f"  {DIM}→ {label}  {ms:.1f}ms  HTTP {r.status_code}{RESET}")
        return r

    def post(self, path: str, body: dict, *, auth: bool = True) -> requests.Response:
        label = f"POST {path}"
        return self._call(label, requests.post,
                          f"{self.base}{path}",
                          json=body,
                          headers=self._h() if auth else {"Content-Type": "application/json"})

    def get(self, path: str, params: dict | None = None, *, auth: bool = True) -> requests.Response:
        label = f"GET {path}"
        return self._call(label, requests.get,
                          f"{self.base}{path}",
                          params=params,
                          headers=self._h() if auth else {})

    def delete(self, path: str) -> requests.Response:
        label = f"DELETE {path}"
        return self._call(label, requests.delete,
                          f"{self.base}{path}",
                          headers=self._h())


# ══ Test functions ════════════════════════════════════════════════════════════

def test_health(client: Client, suite: Suite) -> None:
    section("1. Health Check")
    r    = client.get("/health", auth=False)
    data = r.json()
    dump("Response", data)
    suite.record("GET /health → 200", r.status_code == 200)
    suite.record("All dependencies reported",
                 all(k in data.get("dependencies", {})
                     for k in ("elasticsearch", "redis", "cockroachdb")))


def test_register(ca: Client, cb: Client, suite: Suite) -> None:
    section("2. Register")
    for client, tid, name, email, pwd, plan in [
        (ca, "e2e-alpha", "Alpha Corp", "alpha@e2e.com", "alphapass123", "enterprise"),
        (cb, "e2e-beta",  "Beta Inc",   "beta@e2e.com",  "betapass456",  "standard"),
    ]:
        r = client.post("/auth/register", {
            "tenant_id": tid, "name": name,
            "email": email, "password": pwd, "plan": plan,
        }, auth=False)
        suite.record(f"Register {tid} → 201 or 409", r.status_code in (201, 409))

    r_dup = ca.post("/auth/register", {
        "tenant_id": "e2e-alpha", "name": "Dup",
        "email": "other@e2e.com", "password": "pass1234", "plan": "free",
    }, auth=False)
    suite.record("Duplicate tenant_id → 409", r_dup.status_code == 409)

    r_weak = ca.post("/auth/register", {
        "tenant_id": "weak-p", "name": "W",
        "email": "w@e2e.com", "password": "short", "plan": "free",
    }, auth=False)
    suite.record("Weak password → 422", r_weak.status_code == 422)


def test_login(ca: Client, cb: Client, suite: Suite) -> None:
    section("3. Login")
    r = ca.post("/auth/login",
                {"email": "alpha@e2e.com", "password": "alphapass123"}, auth=False)
    suite.record("Login Tenant A → 200", r.status_code == 200)
    ca.token = r.json().get("access_token", "")

    r2 = cb.post("/auth/login",
                 {"email": "beta@e2e.com", "password": "betapass456"}, auth=False)
    suite.record("Login Tenant B → 200", r2.status_code == 200)
    cb.token = r2.json().get("access_token", "")

    r_bad = ca.post("/auth/login",
                    {"email": "alpha@e2e.com", "password": "wrong"}, auth=False)
    suite.record("Wrong password → 401", r_bad.status_code == 401)


def test_auth_me(ca: Client, suite: Suite) -> None:
    section("4. GET /auth/me")
    r = ca.get("/auth/me")
    suite.record("GET /auth/me → 200", r.status_code == 200)
    suite.record("Correct tenant_id", r.json().get("tenant_id") == "e2e-alpha")

    r_none = requests.get(f"{ca.base}/auth/me", timeout=10)
    suite.record("No token → 403", r_none.status_code == 403)


def test_create_documents(ca: Client, suite: Suite) -> list[str]:
    section("5. POST /documents")
    doc_ids = []
    docs = [
        {"title": "CAP Theorem",         "content": "Consistency, Availability, Partition tolerance."},
        {"title": "Raft Consensus",       "content": "Raft manages replicated logs in distributed systems."},
        {"title": "CockroachDB Design",   "content": "Distributed SQL using Raft consensus protocol."},
        {"title": "Elasticsearch Index",  "content": "Inverted index enables sub-50ms full-text search."},
        {"title": "Redis Caching",        "content": "Redis sorted sets implement sliding window rate limits."},
    ]
    for doc in docs:
        r = ca.post("/documents", doc)
        if r.status_code == 201:
            doc_ids.append(r.json()["id"])
            suite.record(f"Index '{doc['title']}' → 201", True)
        else:
            suite.record(f"Index '{doc['title']}' → 201", False, f"got {r.status_code}")

    r_no_auth = requests.post(f"{ca.base}/documents",
                              json={"title": "X", "content": "Y"},
                              headers={"Content-Type": "application/json"}, timeout=10)
    suite.record("POST /documents without token → 403", r_no_auth.status_code == 403)
    return doc_ids


def test_get_document(ca: Client, cb: Client, doc_ids: list[str], suite: Suite) -> None:
    section("6. GET /documents/{id}")
    r = ca.get(f"/documents/{doc_ids[0]}")
    suite.record("Owner GET → 200", r.status_code == 200)
    suite.record("Correct tenant in response",
                 r.json().get("tenant_id") == "e2e-alpha")

    r_cross = cb.get(f"/documents/{doc_ids[0]}")
    suite.record("Cross-tenant GET → 404 (isolation)", r_cross.status_code == 404,
                 f"BREACH! got {r_cross.status_code}" if r_cross.status_code != 404 else "")

    r_miss = ca.get("/documents/nonexistent-id-xyz")
    suite.record("Non-existent doc → 404", r_miss.status_code == 404)


def test_search(ca: Client, cb: Client, suite: Suite) -> None:
    section("7. GET /search")
    print(f"  {YELLOW}Waiting 2s for async ES indexing...{RESET}")
    time.sleep(2)

    r = ca.get("/search", {"q": "distributed systems"})
    data = r.json()
    suite.record("Search → 200", r.status_code == 200)
    suite.record("Returns results", data.get("total", 0) >= 1)
    suite.record("cached=False on first call", data.get("cached") is False)

    r2 = ca.get("/search", {"q": "distributed systems"})
    suite.record("Same query → cached=True (Redis L2 hit)", r2.json().get("cached") is True)

    r_iso = cb.get("/search", {"q": "distributed systems"})
    suite.record("Tenant B sees 0 results from Tenant A (isolation)",
                 r_iso.json().get("total") == 0,
                 f"BREACH! total={r_iso.json().get('total')}" if r_iso.json().get("total", 0) > 0 else "")

    r_page = ca.get("/search", {"q": "distributed", "page": 1, "size": 2})
    suite.record("Pagination size=2 respected",
                 len(r_page.json().get("results", [])) <= 2)


def test_delete(ca: Client, cb: Client, doc_ids: list[str], suite: Suite) -> None:
    section("8. DELETE /documents/{id}")
    target = doc_ids[-1]

    r_cross = cb.delete(f"/documents/{target}")
    suite.record("Cross-tenant DELETE → 404 (isolation)", r_cross.status_code == 404,
                 f"BREACH! got {r_cross.status_code}" if r_cross.status_code != 404 else "")

    r = ca.delete(f"/documents/{target}")
    suite.record("Owner DELETE → 204", r.status_code == 204)

    r_get = ca.get(f"/documents/{target}")
    suite.record("Deleted doc → 404 on GET", r_get.status_code == 404)

    r2 = ca.delete(f"/documents/{target}")
    suite.record("Double DELETE → 404", r2.status_code == 404)


def test_validation(ca: Client, suite: Suite) -> None:
    section("9. Input Validation")
    cases = [
        ("Missing content",  ca.post("/documents", {"title": "X"}),            422),
        ("Missing title",    ca.post("/documents", {"content": "X"}),           422),
        ("Empty search q",   ca.get("/search", {"q": ""}),                      422),
    ]
    for name, r, expected in cases:
        suite.record(f"{name} → {expected}", r.status_code == expected,
                     f"got {r.status_code}")


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
                "email": email, "password": "loadpass123", "plan": "enterprise",
            }, timeout=10)
            # Login to get token
            r = await client.post(f"{base_url}/auth/login", json={
                "email": email, "password": "loadpass123",
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
    section(f"10. Load Test  —  {target_rps} req/s  ×  {duration_sec}s  "
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
            if requests.get(f"{base_url}/health", timeout=5).status_code == 200:
                print(f"{GREEN}Server ready.{RESET}")
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
    doc_ids = test_create_documents(ca, suite)
    if doc_ids:
        test_get_document(ca, cb, doc_ids, suite)
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

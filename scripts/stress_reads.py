"""Drive concurrent read traffic at a running CloverBoard server.

The Web UI polls a run catalog plus per-run metrics, summaries and scalar
batches. This tool replays that mix with a configurable number of in-flight
requests so thread-pool exhaustion and lock convoys show up as latency or
timeouts instead of a frozen page.

    python scripts/stress_reads.py --url http://127.0.0.1:7230 --duration 30
    python scripts/stress_reads.py --url "http://host:7230/?token=..." --concurrency 80
    python scripts/stress_reads.py --url http://127.0.0.1:7230 --catalog-only

Exit status is 0 unless ``--fail-on-error`` is passed and a request fails.
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CATALOG = "/api/run-catalog"


def _request(url, token, timeout):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
            body = response.read()
            return response.status, body, None, time.perf_counter() - started
    except urllib.error.HTTPError as error:
        error.read()
        return error.code, b"", None, time.perf_counter() - started
    except Exception as error:  # noqa: BLE001 - the report keeps the exception name
        return None, b"", type(error).__name__, time.perf_counter() - started


def _url(base, path, params):
    query = urlencode({key: value for key, value in params.items() if value is not None})
    return base + path + (f"?{query}" if query else "")


def _json(base, path, params, token, timeout):
    status, body, error, _ = _request(_url(base, path, params), token, timeout)
    if status != 200:
        raise RuntimeError(f"{path} returned {status or error}")
    return json.loads(body)


def _cases(base, token, timeout, run_limit, metric_limit):
    catalog = _json(base, CATALOG, {}, token, timeout)
    runs = catalog.get("runs", []) if isinstance(catalog, dict) else catalog
    runs = [run for run in runs if run.get("has_scalars")]
    # Local runs exercise this server; peer runs mostly exercise the network.
    runs.sort(key=lambda run: run.get("source") != "local")
    cases = [("catalog", CATALOG, {}), ("runs", "/api/runs", {})]
    selected = runs[:max(1, run_limit)]
    for run in selected:
        run_id = run["run_id"]
        cases.append(("metrics", "/api/metrics", {"run_id": run_id}))
        metrics = []
        try:
            metrics = [field["metric"] for field in _json(base, "/api/metrics", {"run_id": run_id}, token, timeout)["fields"]]
        except (RuntimeError, KeyError, ValueError):
            pass
        if metrics:
            cases.append(("batch", "/api/scalars-batch", {"run_id": run_id, "metrics": metrics[:16], "max_points": 800}))
            for metric in metrics[:max(1, metric_limit)]:
                cases.append(("summary", "/api/scalar-summary", {"run_id": run_id, "metric": metric}))
                cases.append(("series", "/api/scalars", {"run_id": run_id, "metric": metric, "max_points": 800}))
    return cases, [run["run_id"] for run in selected]


def _worker(index, cases, base, token, timeout, deadline):
    rng = random.Random(index)
    results = []
    while time.monotonic() < deadline:
        name, path, params = rng.choice(cases)
        status, _, error, elapsed = _request(_url(base, path, params), token, timeout)
        results.append((name, status, error, elapsed))
    return results


def _percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def _summarize(rows):
    failures = [row for row in rows if row[1] != 200]
    latencies = [row[3] * 1000 for row in rows]
    errors = {}
    for _, status, error, _ in failures:
        key = str(status) if status is not None else (error or "unknown")
        errors[key] = errors.get(key, 0) + 1
    return {
        "requests": len(rows),
        "ok": len(rows) - len(failures),
        "failed": len(failures),
        "latency_ms": {
            "p50": round(_percentile(latencies, 0.5), 1),
            "p95": round(_percentile(latencies, 0.95), 1),
            "p99": round(_percentile(latencies, 0.99), 1),
            "max": round(max(latencies, default=0.0), 1),
        },
        "errors": errors,
    }


def _parse_url(value):
    parsed = urlsplit(value)
    token = None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    kept = []
    for key, item in query:
        if key == "token" and token is None:
            token = item
        else:
            kept.append((key, item))
    base = urlunsplit((parsed.scheme or "http", parsed.netloc, parsed.path.rstrip("/"), urlencode(kept), ""))
    return base, token


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay the Web UI's read mix at a CloverBoard server.")
    parser.add_argument("--url", default="http://127.0.0.1:7230", help="server base URL; a printed /?token= share URL is accepted")
    parser.add_argument("--token", help="access token (prefer --token-env)")
    parser.add_argument("--token-env", default="CLOVERBOARD_TOKEN", help="environment variable holding the access token (default: %(default)s)")
    parser.add_argument("--concurrency", type=int, default=40, help="in-flight requests (the server's sync worker pool is 40)")
    parser.add_argument("--duration", type=float, default=30.0, help="seconds to keep the load running")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds")
    parser.add_argument("--runs", type=int, default=3, help="runs included in the request mix")
    parser.add_argument("--metrics-per-run", type=int, default=4, help="summary/series requests generated per run")
    parser.add_argument("--catalog-only", action="store_true", help="only hammer /api/run-catalog (isolates the catalog scan)")
    parser.add_argument("--seed", type=int, default=0, help="request-order seed for repeatability")
    parser.add_argument("--json", action="store_true", help="print a machine-readable report")
    parser.add_argument("--fail-on-error", action="store_true", help="exit 1 when any request fails")
    args = parser.parse_args(argv)

    base, url_token = _parse_url(args.url)
    token = args.token or os.environ.get(args.token_env) or url_token
    if not token:
        token = None

    cases, run_ids = _cases(base, token, args.timeout, args.runs, args.metrics_per_run)
    if args.catalog_only:
        cases = [case for case in cases if case[1] == CATALOG]
    if args.seed:
        random.Random(args.seed).shuffle(cases)

    deadline = time.monotonic() + max(0.1, args.duration)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        results = [row for rows in pool.map(
            lambda index: _worker(index, cases, base, token, args.timeout, deadline),
            range(max(1, args.concurrency)),
        ) for row in rows]
    elapsed = time.perf_counter() - started
    report = _summarize(results)
    report.update({
        "url": base,
        "runs": run_ids,
        "cases": len(cases),
        "concurrency": args.concurrency,
        "seconds": round(elapsed, 1),
        "requests_per_second": round(report["requests"] / elapsed, 1) if elapsed else 0.0,
    })

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        latency = report["latency_ms"]
        print(f"url: {base}  runs: {len(run_ids)}  cases: {len(cases)}  concurrency: {args.concurrency}")
        print(f"requests: {report['requests']}  ok: {report['ok']}  failed: {report['failed']}  rps: {report['requests_per_second']}")
        print(f"latency ms: p50={latency['p50']}  p95={latency['p95']}  p99={latency['p99']}  max={latency['max']}")
        if report["errors"]:
            print("errors: " + ", ".join(f"{key}={count}" for key, count in sorted(report["errors"].items())))
        if report["failed"] or latency["p99"] > args.timeout * 500:
            print("hint: if the UI is also frozen, take `py-spy dump --native --pid PID` while this runs.")
    if args.fail_on_error and report["failed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

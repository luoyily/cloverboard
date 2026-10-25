"""Read-only HTTP/CPU baselines and optional automated Charts browser measurements."""

import argparse
from contextlib import asynccontextmanager
import cProfile
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import pstats
import statistics
import sys
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


def distribution(values):
    return {"min_ms": round(min(values), 2), "median_ms": round(statistics.median(values), 2),
            "max_ms": round(max(values), 2), "samples_ms": [round(value, 2) for value in values]}


def timed_call(action, repeats):
    values = []
    for _ in range(repeats):
        start = perf_counter()
        action()
        values.append((perf_counter() - start) * 1000)
    return distribution(values)


def profile_call(action):
    profiler = cProfile.Profile()
    profiler.runcall(action)
    rows = []
    for (file, line, function), (primitive, total, own, cumulative, _) in pstats.Stats(profiler).stats.items():
        rows.append({"file": Path(file).name, "line": line, "function": function, "calls": total,
                     "self_ms": round(own * 1000, 2), "cumulative_ms": round(cumulative * 1000, 2)})
    return sorted(rows, key=lambda row: row["cumulative_ms"], reverse=True)[:12]


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def measure_backend(url, headers, repeats, max_runs, logdir):
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "url": url, "environment": {"python": platform.python_version(), "platform": platform.platform(), "httpx": httpx.__version__},
              "http": [], "browser": [], "notes": ["Timing results are observations, not pass/fail thresholds.",
                  "Repeated samples use a running server and warm filesystem caches; this is not an OS cold-start benchmark."]}
    with httpx.Client(base_url=url, headers=headers, trust_env=False, timeout=20) as client:
        runs = client.get("/api/runs").raise_for_status().json()
        sources = client.get("/api/sources").raise_for_status().json()
        workspace = client.get("/api/workspace").raise_for_status().json()
        kinds = {"local": "local", **{source["source_id"]: source["kind"] for source in sources}}
        local = [run for run in runs if kinds.get(run["source"]) == "local"][:max_runs]
        mixed = runs[:max_runs]
        peer = next((run for run in mixed if kinds.get(run["source"]) == "peer"), None)
        # Include a Peer when the run cap would otherwise hide the distinguishing source.
        if peer is None:
            peer = next((run for run in runs if kinds.get(run["source"]) == "peer"), None)
            if peer and mixed:
                mixed[-1] = peer
        cases = {"local": [run["run_id"] for run in local], "mixed": [run["run_id"] for run in mixed]}
        cases = {name: ids for name, ids in cases.items() if ids}
        report["cases"] = cases
        report["sources"] = [{"source_id": source["source_id"], "kind": source["kind"], "online": source["online"]} for source in sources]
        probes = [("run_list_local_sources", "/api/runs", {"local_only": "true"}), ("run_list_all_sources", "/api/runs", {})]
        for label, run in [("local", local[0] if local else None), ("peer", peer)]:
            if run is None:
                continue
            run_id = run["run_id"]
            metrics = client.get("/api/metrics", params={"run_id": run_id}).raise_for_status().json()["metrics"]
            metric = "train/loss" if "train/loss" in metrics else next(iter(metrics), None)
            probes.append((f"{label}_metric_catalog", "/api/metrics", {"run_id": run_id}))
            if metric:
                probes.extend([(f"{label}_scalar_series", "/api/scalars", {"run_id": run_id, "metric": metric, "max_points": 800, "smoothing": .85}),
                               (f"{label}_scalar_summary", "/api/scalar-summary", {"run_id": run_id, "metric": metric})])
        for name, route, params in probes:
            sizes, samples, statuses, errors = [], [], [], []
            for _ in range(repeats):
                start = perf_counter()
                try:
                    response = client.get(route, params=params)
                    response.raise_for_status()
                    response.json()
                    sizes.append(len(response.content))
                    statuses.append(response.status_code)
                except httpx.HTTPError as error:
                    errors.append(type(error).__name__)
                samples.append((perf_counter() - start) * 1000)
            report["http"].append({"name": name, "route": route, "params": params, **distribution(samples),
                                   "response_bytes": sizes, "statuses": statuses, "errors": errors})

    def new_client():
        with httpx.Client(trust_env=False, timeout=3):
            pass

    report["client_initialization"] = {**timed_call(new_client, repeats), "profile": profile_call(new_client)}
    if logdir:
        from cloverboard.storage.sqlite_backend import SqliteStorageReader
        reader = SqliteStorageReader(logdir)
        local_runs = reader.list_runs()
        metrics = reader.list_metrics(local_runs[0].run_id) if local_runs else []
        if metrics:
            run_id = local_runs[0].run_id
            metric = "train/loss" if "train/loss" in metrics else metrics[0]
            read = lambda: reader.get_scalar_series(run_id, metric, 800, smoothing=.85)
            report["storage"] = {"run_id": run_id, "metric": metric, "raw_count": reader.scalar_count(run_id, metric),
                                 "returned_count": len(read()), **timed_call(read, repeats), "profile": profile_call(read)}
    return report, workspace


def browser_summary(rows):
    result = {}
    for case in sorted({row["case"] for row in rows}):
        matching = [row for row in rows if row["case"] == case]
        valid = [row for row in matching if row.get("first_curve_paint_ms") is not None and not row.get("error") and not row.get("errors")]
        result[case] = {"successful": len(valid), "total": len(matching)}
        for field in ("first_curve_dom_ms", "first_curve_paint_ms", "paint_delay_ms", "last_series_json_ms", "post_data_paint_ms", "long_task_total_ms"):
            values = [row[field] for row in valid if row.get(field) is not None]
            if values:
                result[case][field] = distribution(values)
    return result


def create_probe_app(url, headers, report, workspace, repeats, output, timeout):
    import asyncio
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    identity = uuid4().hex
    completed = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(base_url=url, headers=headers, trust_env=False, timeout=30) as client:
            app.state.client = client
            async def expire():
                try:
                    await asyncio.wait_for(completed.wait(), timeout)
                except asyncio.TimeoutError:
                    report["browser_error"] = "Browser measurement timed out before completion"
                    write_report(output, report)
                app.state.server.should_exit = True
            timer = asyncio.create_task(expire())
            try:
                yield
            finally:
                timer.cancel()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    harness = (PROJECT / "scripts" / "perf_browser.html").read_text(encoding="utf-8")
    observer = (PROJECT / "scripts" / "perf_observer.js").read_text(encoding="utf-8")

    @app.get("/__perf/")
    async def control():
        config = json.dumps({"cases": list(report["cases"]), "repeats": repeats}).replace("<", "\\u003c")
        return HTMLResponse(harness.replace("__PERF_CONFIG__", config), headers={"Cache-Control": "no-store"})

    @app.post("/__perf/result")
    async def finish(request: Request):
        value = await request.json()
        if not isinstance(value, list) or len(value) != repeats * len(report["cases"]):
            return JSONResponse({"error": "Unexpected result count"}, status_code=400)
        if any(row.get("case") not in report["cases"] for row in value):
            return JSONResponse({"error": "Unexpected scenario"}, status_code=400)
        report["browser"] = value
        report["browser_summary"] = browser_summary(value)
        report["notes"].append("Browser runs use an isolated read-only proxy with fresh documents. Static resources and OS caches may be warm; proxy overhead is included.")
        write_report(output, report)
        completed.set()
        return JSONResponse({"saved": str(output), "summary": report["browser_summary"]})

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
    async def forward(path: str, request: Request):
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"detail": "Performance probe is read-only"}, status_code=403)
        if path == "api/workspace":
            case = request.headers.get("x-cloverboard-perf-case", "local")
            iteration = request.headers.get("x-cloverboard-perf-round", "0")
            selected = report["cases"].get(case, [])
            state = {**(workspace.get("state") or {}), "panel": "charts", "selected_ids": selected,
                     "refresh_interval": 0,
                     "chart_zoom": {}, "chart_warmup": {}, "expanded_charts": [],
                     "collapsed_groups": ["diagnostics", "optimizer"], "active_view_id": None}
            if state.get("baseline_id") not in selected:
                state["baseline_id"] = None
            return JSONResponse({**workspace, "workspace_id": f"perf:{identity}:{case}:{iteration}", "writable": False,
                                 "state": state, "saved_at": 0, "views": []}, headers={"Cache-Control": "no-store"})
        query = [(key, value) for key, value in request.query_params.multi_items() if not key.startswith("__perf_")]
        forwarded = {key: request.headers[key] for key in ("if-none-match", "if-modified-since", "range", "if-range") if key in request.headers}
        try:
            response = await app.state.client.request(request.method, "/" + path, params=query, headers=forwarded)
        except httpx.HTTPError as error:
            return JSONResponse({"detail": type(error).__name__}, status_code=502)
        response_headers = {key: value for key, value in response.headers.items()
                            if key.lower() in {"content-type", "etag", "cache-control", "last-modified", "content-range", "accept-ranges"}}
        content = response.content
        if not path and response.status_code == 200:
            config = json.dumps({"case": request.query_params.get("__perf_case", "local"), "round": int(request.query_params.get("__perf_round", "0"))}).replace("<", "\\u003c")
            script = observer.replace("__PERF_CONFIG__", config)
            content = response.text.replace("<head>", "<head><script>" + script + "</script>", 1).encode("utf-8")
            response_headers["cache-control"] = "no-store"
        return Response(content, status_code=response.status_code, headers=response_headers)

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:6030", help="running CloverBoard origin")
    parser.add_argument("--token-env", help="environment variable containing an optional Bearer token")
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 11), metavar="1-10")
    parser.add_argument("--max-runs", type=int, default=5, choices=range(1, 21), metavar="1-20")
    parser.add_argument("--logdir", type=Path, help="optional local log directory for direct storage timing")
    parser.add_argument("--output", type=Path, help="JSON report; defaults to a timestamped .preview/performance file")
    parser.add_argument("--browser", action="store_true", help="serve an isolated automated browser probe after HTTP measurements")
    parser.add_argument("--port", type=int, default=6045, help="loopback browser probe port")
    parser.add_argument("--browser-timeout", type=int, default=180, help="seconds to wait for the browser, then stop")
    args = parser.parse_args(argv)
    parts = urlsplit(args.url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
        parser.error("Use an HTTP(S) origin without credentials, a query, or a path")
    token = os.environ.get(args.token_env) if args.token_env else None
    if args.token_env and not token:
        parser.error("The token environment variable is empty")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    output = args.output or PROJECT / ".preview" / "performance" / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    try:
        report, workspace = measure_backend(args.url.rstrip("/"), headers, args.repeats, args.max_runs, args.logdir)
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise SystemExit(f"Unable to measure the service: {type(error).__name__}") from error
    write_report(output, report)
    for row in report["http"]:
        print(f"{row['name']}: median {row['median_ms']:.2f} ms ({len(row['errors'])} errors)", flush=True)
    print(f"New HTTP client: median {report['client_initialization']['median_ms']:.2f} ms", flush=True)
    print(f"Report: {output.resolve()}", flush=True)
    if args.browser:
        import uvicorn
        app = create_probe_app(args.url.rstrip("/"), headers, report, workspace, args.repeats, output, args.browser_timeout)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False))
        app.state.server = server
        print(f"Open automated browser probe: http://127.0.0.1:{args.port}/__perf/", flush=True)
        server.run()
        print("Browser probe stopped. Results saved to the same report.", flush=True)


if __name__ == "__main__":
    main()

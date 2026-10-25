import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from scripts.perf import browser_summary, create_probe_app, distribution


def test_performance_summary_keeps_failed_rounds_out_of_latency_statistics():
    rows = [
        {"case": "local", "first_curve_paint_ms": 120, "last_series_json_ms": 100, "post_data_paint_ms": 20, "long_task_total_ms": 0},
        {"case": "local", "first_curve_paint_ms": 180, "last_series_json_ms": 140, "post_data_paint_ms": 40, "long_task_total_ms": 60},
        {"case": "local", "first_curve_paint_ms": 40000, "error": "Timeout"},
        {"case": "local", "first_curve_paint_ms": 10, "errors": ["Script failed"]},
    ]
    summary = browser_summary(rows)["local"]
    assert summary["successful"] == 2 and summary["total"] == 4
    assert summary["first_curve_paint_ms"]["median_ms"] == 150
    assert distribution([3, 1, 2])["samples_ms"] == [3, 1, 2]


def test_browser_probe_isolates_workspace_blocks_writes_and_saves_results(tmp_path, monkeypatch):
    calls = []

    def remote(request):
        calls.append((request.method, request.url.path))
        assert not any(key.startswith("__perf_") for key in request.url.params)
        if request.url.path == "/":
            return httpx.Response(200, text='<html><head></head><body><div id="root"></div></body></html>', headers={"Content-Type": "text/html"})
        return httpx.Response(200, json=[])

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(remote), **kwargs))
    output = tmp_path / "result.json"
    report = {"cases": {"local": ["one"], "mixed": ["one", "peer:two"]}, "browser": [], "notes": []}
    original = {"workspace_id": "original", "writable": True, "saved_at": 1,
                "state": {"selected_ids": ["untouched"], "panel": "table", "baseline_id": "untouched"}, "views": []}
    app = create_probe_app("http://service", {}, report, original, 1, output, 60)
    app.state.server = SimpleNamespace(should_exit=False)
    with TestClient(app) as client:
        snapshot = client.get("/api/workspace", headers={"X-Cloverboard-Perf-Case": "mixed", "X-Cloverboard-Perf-Round": "1"}).json()
        assert snapshot["workspace_id"] != "original" and snapshot["writable"] is False
        assert snapshot["state"]["panel"] == "charts" and snapshot["state"]["refresh_interval"] == 0
        assert snapshot["state"]["selected_ids"] == ["one", "peer:two"] and snapshot["state"]["baseline_id"] is None
        assert original["state"]["panel"] == "table" and original["state"]["selected_ids"] == ["untouched"]
        assert client.put("/api/workspace/state", json={}).status_code == 403
        assert client.patch("/api/run-tags?run_id=one", json={}).status_code == 403
        page = client.get("/?__perf_case=mixed&__perf_round=1")
        assert "MutationObserver" in page.text and "X-Cloverboard-Perf-Case" in page.text
        assert "Automated fresh-document" in client.get("/__perf/").text
        assert calls == [("GET", "/")]
        assert client.post("/__perf/result", json=[]).status_code == 400
        rows = [{"case": case, "first_curve_paint_ms": 100, "last_series_json_ms": 90,
                 "post_data_paint_ms": 10, "long_task_total_ms": 0} for case in report["cases"]]
        assert client.post("/__perf/result", json=rows).status_code == 200
        assert json.loads(output.read_text())["browser_summary"]["mixed"]["successful"] == 1

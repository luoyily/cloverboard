from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from cloverboard.storage.protocols import RunSummary, ScalarPoint
from tests.fakes import FakeStorageReader


def test_runs_endpoint_returns_empty_without_backend():
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/api/runs")

    assert response.status_code == 200
    assert response.json() == []


def test_scalars_endpoint_returns_not_found_without_backend():
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/api/scalars", params={"run_id": "r1", "metric": "loss"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


def test_runs_endpoint_serializes_reader_output():
    reader = FakeStorageReader(
        runs=[
            RunSummary(
                run_id="r1",
                name="demo",
                started_at=123.0,
                config={"lr": 0.1},
            )
        ]
    )
    client = TestClient(create_app(reader), base_url="http://127.0.0.1")

    payload = client.get("/api/runs").json()

    assert payload == [
        {
            "run_id": "r1",
            "name": "demo",
            "started_at": 123.0,
            "config": {"lr": 0.1},
            "source": "local",
            "has_notes": False, "note_excerpt": "", "tags": [], "tags_writable": True, "has_scalars": False, "last_recorded_at": None,
        }
    ]


def test_scalars_endpoint_serializes_points():
    reader = FakeStorageReader(
        series={("r1", "train/loss"): [ScalarPoint(step=1, value=0.5)]}
    )
    client = TestClient(create_app(reader), base_url="http://127.0.0.1")

    payload = client.get(
        "/api/scalars", params={"run_id": "r1", "metric": "train/loss"}
    ).json()

    assert payload["points"] == [{"step": 1, "value": 0.5}]


def test_scalars_endpoint_validates_max_points():
    client = TestClient(create_app(FakeStorageReader(runs=[RunSummary("r1", None, 0)])), base_url="http://127.0.0.1")

    response = client.get(
        "/api/scalars",
        params={"run_id": "r1", "metric": "loss", "max_points": 1},
    )

    assert response.status_code == 422


def test_batch_scalars_is_a_bounded_authenticated_read_with_single_series_semantics():
    reader = FakeStorageReader(series={("r1", "loss"): [ScalarPoint(0, 2), ScalarPoint(1, float("nan")), ScalarPoint(2, 4)]})
    app = create_app(reader, share=True)
    client = TestClient(app, base_url="http://127.0.0.1")
    params = [("run_id", "r1"), ("metrics", "loss"), ("metrics", "missing"), ("smoothing", .5)]
    assert client.get("/api/scalars-batch", params=params).status_code == 401
    client.headers["Authorization"] = f"Bearer {app.state.access_token}"
    response = client.get("/api/scalars-batch", params=params)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    batch = response.json()
    assert [row["metric"] for row in batch] == ["loss", "missing"]
    single = client.get("/api/scalars", params={"run_id": "r1", "metric": "loss", "smoothing": .5}).json()
    assert batch[0] == single
    assert batch[0]["points"][1] == {"step": 1, "value": None, "smoothed": None}
    assert batch[1]["points"] == []
    assert client.get("/api/scalars-batch", params=[("run_id", "r1")] + [("metrics", str(i)) for i in range(17)]).status_code == 422
    assert client.get("/api/scalars-batch", params={"run_id": "r1", "metrics": ["a", "b", "c", "d"], "max_points": 10000}).status_code == 422
    assert client.get("/api/scalars-batch", params={"run_id": "r1", "metrics": "loss", "step_min": 5, "step_max": 1}).status_code == 422
    assert client.get("/api/scalars-batch", params={"run_id": "../outside", "metrics": "loss"}).status_code == 403


def test_scalars_endpoint_maps_non_finite_values_to_null():
    reader = FakeStorageReader(
        series={
            ("r1", "loss"): [
                ScalarPoint(step=1, value=float("nan")),
                ScalarPoint(step=2, value=0.5),
            ]
        }
    )
    client = TestClient(create_app(reader), base_url="http://127.0.0.1")

    payload = client.get(
        "/api/scalars", params={"run_id": "r1", "metric": "loss"}
    ).json()

    assert payload["points"] == [
        {"step": 1, "value": None},
        {"step": 2, "value": 0.5},
    ]


def test_static_site_is_mounted(tmp_path):
    (tmp_path / "index.html").write_text("<html>cloverboard</html>", encoding="utf-8")
    client = TestClient(create_app(static_dir=tmp_path), base_url="http://127.0.0.1")

    response = client.get("/")

    assert response.status_code == 200
    assert "cloverboard" in response.text


def test_metrics_endpoint_returns_not_found_without_backend():
    client = TestClient(create_app(), base_url="http://127.0.0.1")

    response = client.get("/api/metrics", params={"run_id": "r1"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


def test_metrics_endpoint_lists_reader_metrics():
    reader = FakeStorageReader(
        series={
            ("r1", "train/loss"): [ScalarPoint(step=1, value=0.5)],
            ("r1", "val/acc"): [ScalarPoint(step=1, value=0.9)],
            ("r2", "train/loss"): [ScalarPoint(step=1, value=0.4)],
        }
    )
    client = TestClient(create_app(reader), base_url="http://127.0.0.1")

    payload = client.get("/api/metrics", params={"run_id": "r1"}).json()

    assert payload == {"run_id": "r1", "metrics": ["train/loss", "val/acc"], "fields": [{"metric": "train/loss", "group": "train", "name": "loss"}, {"metric": "val/acc", "group": "val", "name": "acc"}]}

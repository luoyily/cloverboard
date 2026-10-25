import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from cloverboard.server.security import ACCESS_COOKIE
from cloverboard.storage.protocols import RunMeta, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter


def _shared_app(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>cloverboard</html>", encoding="utf-8")
    (static / "app.js").write_text("const app = true;", encoding="utf-8")
    return create_app(static_dir=static, logdir=tmp_path / "logs", share=True)


@pytest.mark.parametrize("path", ["/", "/app.js", "/api/runs", "/api/workspace", "/docs", "/openapi.json"])
def test_share_authenticates_every_http_surface(tmp_path, path):
    app = _shared_app(tmp_path)
    client = TestClient(app)
    assert client.get(path).status_code == 401
    assert client.get(path, params={"token": "incorrect"}).status_code == 401
    response = client.get(path, headers={"Authorization": f"Bearer {app.state.access_token}"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_share_link_establishes_cookie_and_strips_only_token(tmp_path):
    app = _shared_app(tmp_path)
    client = TestClient(app)
    token = app.state.access_token
    response = client.get("/", params={"token": token, "foo": ["a", "b"]}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?foo=a&foo=b"
    cookie = response.headers["set-cookie"]
    assert f"{ACCESS_COOKIE}_80={token}" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Max-Age" not in cookie
    assert client.get(response.headers["location"]).status_code == 200
    assert client.get("/app.js").status_code == 200
    snapshot = client.get("/api/workspace").json()
    assert snapshot["writable"] is False
    assert client.get("/api/runs?token=incorrect").status_code == 401
    assert client.get("/api/runs", headers={"Authorization": "Basic invalid"}).status_code == 401
    assert client.get("/api/runs?token=☃").status_code == 401


def test_https_share_cookie_is_secure(tmp_path):
    app = _shared_app(tmp_path)
    client = TestClient(app, base_url="https://example.test")
    response = client.get("/", params={"token": app.state.access_token}, follow_redirects=False)
    assert "Secure" in response.headers["set-cookie"]
    assert client.get("/api/runs").status_code == 200


def test_tokens_rotate_and_stale_session_is_rejected(tmp_path):
    first, second = create_app(share=True), create_app(share=True)
    assert first.state.access_token != second.state.access_token
    assert len(first.state.access_token) >= 22
    client = TestClient(second)
    client.cookies.set(f"{ACCESS_COOKIE}_80", first.state.access_token)
    assert client.get("/api/runs").status_code == 401
    response = client.get("/api/runs", headers={"Authorization": f"Bearer {second.state.access_token}"})
    assert response.status_code == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"])
@pytest.mark.parametrize("share", [False, True])
def test_read_only_protects_all_routes_before_route_validation(tmp_path, method, share):
    app = create_app(logdir=tmp_path, read_only=not share, share=share)
    called = []

    @app.api_route("/future-write", methods=[method])
    def future_write():
        called.append(True)
        return {"ok": True}

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {app.state.access_token}"} if share else {}
    if share:
        assert client.request(method, "/future-write").status_code == 401
    assert client.request(method, "/future-write", headers=headers).status_code == 403
    assert client.request(method, "/unknown-route", headers=headers).status_code == 403
    assert client.request(method, "/api/workspace/state", headers=headers, content="not json").status_code == 403
    assert not called
    assert not (tmp_path / ".workspace").exists()


def test_shared_get_and_head_remain_readable(tmp_path):
    app = _shared_app(tmp_path)
    client = TestClient(app, headers={"Authorization": f"Bearer {app.state.access_token}"})
    assert client.head("/app.js").status_code == 200
    assert client.get("/api/runs").status_code == 200


def test_shared_servers_on_different_ports_keep_independent_sessions():
    first, second = create_app(share=True), create_app(share=True)
    first_client = TestClient(first, base_url="http://localhost:6006")
    second_client = TestClient(second, base_url="http://localhost:6007")
    first_client.get("/api/runs", params={"token": first.state.access_token})
    second_client.cookies.update(first_client.cookies)
    second_client.get("/api/runs", params={"token": second.state.access_token})
    first_client.cookies.update(second_client.cookies)
    assert first_client.get("/api/runs").status_code == 200
    assert second_client.get("/api/runs").status_code == 200


@pytest.mark.parametrize(("path", "destination"), [
    ("/%5c%5cexample.test/path", "/example.test/path"),
    ("/%2f%2fexample.test/path", "/example.test/path"),
    ("/space%20name%3F%23%25", "/space%20name%3F%23%25"),
    ("/%E6%96%87%E6%9C%AC", "/%E6%96%87%E6%9C%AC"),
])
def test_share_redirect_keeps_paths_local_and_escaped(path, destination):
    app = create_app(share=True)
    response = TestClient(app).get(
        path, params={"token": app.state.access_token}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == destination


def test_query_credentials_are_removed_from_scope_and_access_logs(tmp_path):
    app = create_app(share=True)
    observed = []

    async def inspect_scope(scope, receive, send):
        async def inspect_response(message):
            if message["type"] == "http.response.start":
                observed.append(scope["query_string"])
            await send(message)
        await app(scope, receive, inspect_response)

    client = TestClient(inspect_scope)
    token = app.state.access_token
    for value in (token, "incorrect"):
        client.get("/api/runs", params={"token": value, "x": "1"}, follow_redirects=False)
    assert observed == [b"x=1", b"x=1"]


@pytest.mark.parametrize("run_id", ["../../outside", "..\\outside", "/absolute", "C:\\outside", "r/../other", "", ".workspace", "r:secret", "r\0bad", ".. "])
@pytest.mark.parametrize("endpoint", ["metrics", "scalars"])
def test_invalid_run_paths_return_403_even_without_a_reader(run_id, endpoint):
    client = TestClient(create_app())
    response = client.get(f"/api/{endpoint}", params={"run_id": run_id, "metric": "loss"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Path is not allowed"}


def _write_run(directory):
    directory.mkdir(parents=True, exist_ok=True)
    writer = SqliteStorageWriter(directory / "run.sqlite")
    writer.init_run(RunMeta(run_id=directory.name, name="private", started_at=0))
    writer.append_scalars([ScalarRecord("loss", 1, 0, 0.5)])
    writer.flush_and_close()


def test_directory_link_cannot_escape_logdir(tmp_path, directory_link):
    logs = tmp_path / "logs"
    logs.mkdir()
    outside = tmp_path / "outside"
    _write_run(outside)
    directory_link(logs / "linked", outside)
    client = TestClient(create_app(SqliteStorageReader(logs), logdir=logs))
    assert client.get("/api/runs").json() == []
    for endpoint in ("metrics", "scalars"):
        assert client.get(f"/api/{endpoint}", params={"run_id": "linked", "metric": "loss"}).status_code == 403


def test_database_file_link_cannot_escape_logdir(tmp_path):
    logs = tmp_path / "logs"
    run = logs / "linked"
    run.mkdir(parents=True)
    outside = tmp_path / "outside"
    _write_run(outside)
    try:
        (run / "run.sqlite").symlink_to(outside / "run.sqlite")
    except OSError as error:
        pytest.skip(f"File symlink permission unavailable: {error}")
    client = TestClient(create_app(SqliteStorageReader(logs), logdir=logs))
    assert client.get("/api/runs").json() == []
    assert client.get("/api/metrics", params={"run_id": "linked"}).status_code == 403


def test_workspace_link_cannot_read_or_write_outside_logdir(tmp_path, directory_link):
    logs, outside = tmp_path / "logs", tmp_path / "private"
    logs.mkdir()
    outside.mkdir()
    saved = outside / "workspace.sqlite"
    saved.write_text('{"state": {"search": "private"}, "saved_at": 10}', encoding="utf-8")
    before = saved.read_bytes()
    directory_link(logs / ".workspace", outside)
    client = TestClient(create_app(SqliteStorageReader(logs), logdir=logs))
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/workspace").status_code == 403
    response = client.put("/api/workspace/state", json={"state": {"search": "changed"}, "saved_at": 100})
    assert response.status_code == 403
    assert saved.read_bytes() == before
    assert list(outside.iterdir()) == [saved]


def test_missing_runs_return_not_found(tmp_path):
    client = TestClient(create_app(SqliteStorageReader(tmp_path)))
    assert client.get("/api/metrics", params={"run_id": "missing"}).status_code == 404
    assert client.get("/api/scalars", params={"run_id": "missing", "metric": "loss"}).status_code == 404

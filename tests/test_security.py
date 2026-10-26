import json
import socket

import pytest
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from cloverboard.server.security import ACCESS_COOKIE, host_allowlist
from cloverboard.storage.protocols import RunMeta, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageReader, SqliteStorageWriter

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _shared_app(tmp_path):
    return _local_app(tmp_path, share=True)


def _local_app(tmp_path, **kwargs):
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<html>cloverboard</html>", encoding="utf-8")
    (static / "app.js").write_text("const app = true;", encoding="utf-8")
    return create_app(static_dir=static, logdir=tmp_path / "logs", **kwargs)


@pytest.mark.parametrize("path", ["/", "/app.js", "/api/runs", "/api/workspace", "/docs", "/openapi.json"])
def test_share_authenticates_every_http_surface(tmp_path, path):
    app = _shared_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get(path).status_code == 401
    assert client.get(path, params={"token": "incorrect"}).status_code == 401
    response = client.get(path, headers={"Authorization": f"Bearer {app.state.access_token}"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_share_link_establishes_cookie_and_strips_only_token(tmp_path):
    app = _shared_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
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
    client = TestClient(second, base_url="http://127.0.0.1")
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

    client = TestClient(app, base_url="http://127.0.0.1")
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
    client = TestClient(app, headers={"Authorization": f"Bearer {app.state.access_token}"}, base_url="http://127.0.0.1")
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
    response = TestClient(app, base_url="http://127.0.0.1").get(
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

    client = TestClient(inspect_scope, base_url="http://127.0.0.1")
    token = app.state.access_token
    for value in (token, "incorrect"):
        client.get("/api/runs", params={"token": value, "x": "1"}, follow_redirects=False)
    assert observed == [b"x=1", b"x=1"]


@pytest.mark.parametrize("run_id", ["../../outside", "..\\outside", "/absolute", "C:\\outside", "r/../other", "", ".workspace", "r:secret", "r\0bad", ".. "])
@pytest.mark.parametrize("endpoint", ["metrics", "scalars"])
def test_invalid_run_paths_return_403_even_without_a_reader(run_id, endpoint):
    client = TestClient(create_app(), base_url="http://127.0.0.1")
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
    client = TestClient(create_app(SqliteStorageReader(logs), logdir=logs), base_url="http://127.0.0.1")
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
    client = TestClient(create_app(SqliteStorageReader(logs), logdir=logs), base_url="http://127.0.0.1")
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
    client = TestClient(create_app(SqliteStorageReader(logs), logdir=logs), base_url="http://127.0.0.1")
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/workspace").status_code == 403
    response = client.put("/api/workspace/state", json={"state": {"search": "changed"}, "saved_at": 100})
    assert response.status_code == 403
    assert saved.read_bytes() == before
    assert list(outside.iterdir()) == [saved]


def test_missing_runs_return_not_found(tmp_path):
    client = TestClient(create_app(SqliteStorageReader(tmp_path)), base_url="http://127.0.0.1")
    assert client.get("/api/metrics", params={"run_id": "missing"}).status_code == 404
    assert client.get("/api/scalars", params={"run_id": "missing", "metric": "loss"}).status_code == 404


@pytest.mark.parametrize("path", ["/", "/app.js", "/api/runs", "/openapi.json"])
@pytest.mark.parametrize("host", ["evil.test", "127.0.0.1.evil.test", "localhost.evil.test", "sub.localhost", "evil.test:6006"])
def test_tokenless_servers_reject_foreign_hosts(tmp_path, host, path):
    client = TestClient(_local_app(tmp_path), base_url="http://127.0.0.1")
    assert client.get(path, headers={"Host": host}).status_code == 421


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:6006", "localhost", "localhost:6006", "[::1]", "[::1]:6006", "LOCALHOST:6006"])
def test_tokenless_servers_accept_loopback_hosts(tmp_path, host):
    client = TestClient(_local_app(tmp_path), base_url="http://127.0.0.1")
    assert client.get("/api/runs", headers={"Host": host}).status_code == 200


def test_bound_non_loopback_host_extends_the_allowlist(tmp_path):
    client = TestClient(_local_app(tmp_path, host="192.168.1.5"), base_url="http://127.0.0.1")
    assert client.get("/api/runs", headers={"Host": "192.168.1.5:6006"}).status_code == 200
    assert client.get("/api/runs", headers={"Host": "127.0.0.1"}).status_code == 200
    assert client.get("/api/runs", headers={"Host": "evil.test"}).status_code == 421


def test_host_allowlist_is_exact_for_explicit_hosts():
    assert host_allowlist("127.0.0.1") == {"127.0.0.1", "localhost", "::1"}
    assert host_allowlist("192.168.1.5") == {"127.0.0.1", "localhost", "::1", "192.168.1.5"}
    assert host_allowlist("[fd00::7]") == {"127.0.0.1", "localhost", "::1", "fd00::7"}


def test_wildcard_host_adds_interface_addresses(monkeypatch):
    monkeypatch.setattr("cloverboard.server.security._local_interface_addresses",
                        lambda: {"192.168.1.5", "fd00::7"})
    assert host_allowlist("0.0.0.0") == {"127.0.0.1", "localhost", "::1", "0.0.0.0", "192.168.1.5", "fd00::7"}
    assert host_allowlist("[::]") == {"127.0.0.1", "localhost", "::1", "::", "192.168.1.5", "fd00::7"}


def test_wildcard_enumeration_failure_degrades_to_the_bound_names(monkeypatch):
    def unavailable():
        raise socket.gaierror("hostname resolution unavailable")
    monkeypatch.setattr("cloverboard.server.security._local_interface_addresses", unavailable)
    assert host_allowlist("0.0.0.0") == {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def test_wildcard_servers_accept_interface_hosts_and_reject_foreign_ones(tmp_path, monkeypatch):
    monkeypatch.setattr("cloverboard.server.security._local_interface_addresses",
                        lambda: {"192.168.1.5", "fd00::7"})
    client = TestClient(_local_app(tmp_path, host="0.0.0.0"), base_url="http://127.0.0.1")
    assert client.get("/api/runs", headers={"Host": "192.168.1.5:6006"}).status_code == 200
    assert client.get("/api/runs", headers={"Host": "192.168.1.5"}).status_code == 200
    assert client.get("/api/runs", headers={"Host": "[fd00::7]:6006"}).status_code == 200
    assert client.get("/api/runs", headers={"Host": "evil.test"}).status_code == 421
    assert client.get("/api/runs", headers={"Host": "192.168.1.5.evil.test"}).status_code == 421


def test_share_servers_keep_serving_foreign_tunnel_hosts(tmp_path):
    app = _shared_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    headers = {"Host": "frp.example.test", "Authorization": f"Bearer {app.state.access_token}"}
    assert client.get("/api/runs", headers=headers).status_code == 200
    assert client.get("/api/runs", headers={"Host": "frp.example.test"}).status_code == 401


def test_mcp_rejects_undecodable_bodies_with_parse_error(tmp_path):
    with TestClient(_local_app(tmp_path), base_url="http://127.0.0.1") as client:
        response = client.post("/mcp", headers=MCP_HEADERS, content=b"\x00\x01garbage")
        assert response.status_code == 400
        assert response.json()["error"] == {"code": -32700, "message": "Parse error"}
        assert client.post("/mcp", headers=MCP_HEADERS, content=b"not json").status_code == 400
        utf16 = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-16")
        assert client.post("/mcp", headers=MCP_HEADERS, content=utf16).status_code == 400
        result = client.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert result.status_code == 200 and result.json()["result"]["tools"]


def test_mcp_rejects_oversized_bodies(tmp_path):
    client = TestClient(_local_app(tmp_path), base_url="http://127.0.0.1")
    response = client.post("/mcp", headers=MCP_HEADERS, content=b"A" * (4 * 1024 * 1024 + 1))
    assert response.status_code == 413


def test_share_mcp_authenticates_before_body_parsing(tmp_path):
    app = _shared_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/mcp", headers=MCP_HEADERS, content=b"\x00\x01garbage").status_code == 401
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {app.state.access_token}"}
    assert client.post("/mcp", headers=headers, content=b"\x00\x01garbage").status_code == 400

"""Connection reuse must preserve peer isolation, one-hop reads and lifecycle."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from cloverboard.server.app import create_app
from cloverboard.server.sources import Source, SourceReader, load_sources
from tests.fakes import FakeStorageReader


def test_concurrent_peer_requests_share_client_without_sharing_credentials(monkeypatch):
    import cloverboard.server.sources as module

    client_class = httpx.Client
    clients, seen = [], []

    def respond(request):
        assert "token" not in request.url.params
        seen.append((request.url.host, request.headers.get("authorization"), request.url.params["local_only"]))
        return httpx.Response(200, json=[])

    def make_client(**options):
        assert options == {"timeout": 3, "follow_redirects": False, "trust_env": False}
        # No verify=False: keep the normal TLS verification policy.
        client = client_class(**options, transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    monkeypatch.setattr(module, "httpx", SimpleNamespace(Client=make_client, HTTPError=httpx.HTTPError))
    sources = load_sources("a=https://a.test/?token=a-secret,b=https://b.test")
    reader = SourceReader(None, sources)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(lambda i: reader._json(sources[i % 2], "/api/runs"), range(16))) == [[]] * 16
        assert len(clients) == 1
        assert set(seen) == {("a.test", "Bearer a-secret", "true"), ("b.test", None, "true")}
    finally:
        reader.close()
    assert clients[0].is_closed
    reader.close()


def test_application_owns_peer_pool_until_shutdown(monkeypatch):
    import cloverboard.server.sources as module

    client_class = httpx.Client
    clients = []

    def make_client(**options):
        client = client_class(**options, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
        clients.append(client)
        return client

    monkeypatch.setattr(module, "httpx", SimpleNamespace(Client=make_client, HTTPError=httpx.HTTPError))
    app = create_app(FakeStorageReader(), sources=[Source("peer", "Peer", url="https://peer.test")])
    with TestClient(app, base_url="http://127.0.0.1") as browser:
        assert len(clients) == 1 and not clients[0].is_closed
        assert browser.get("/api/runs").status_code == 200
        assert browser.get("/api/runs?local_only=true").json() == []
        assert len(clients) == 1
    assert clients[0].is_closed

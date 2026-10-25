import pytest

from cloverboard.cli import build_parser


def test_serve_defaults():
    args = build_parser().parse_args(["serve"])

    assert args.logdir == "./runs"
    assert args.host == "127.0.0.1"
    assert args.port == 6006
    assert args.read_only is False
    assert args.share is False
    assert args.peers == ""


def test_serve_passes_workspace_location_and_read_only_mode(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from cloverboard.cli import main

    captured = {}

    def capture_app(app, **options):
        captured["app"] = app

    monkeypatch.setattr("uvicorn.run", capture_app)
    main(["serve", "--logdir", str(tmp_path), "--read-only"])
    client = TestClient(captured["app"])
    assert client.get("/api/workspace").json()["writable"] is False
    response = client.put("/api/workspace/state", json={"state": {}, "saved_at": 100})
    assert response.status_code == 403
    assert not (tmp_path / ".workspace").exists()


@pytest.mark.parametrize("host, expected_host", [("0.0.0.0", "127.0.0.1"), ("::", "::1")])
def test_share_prints_a_fresh_working_link_and_forces_read_only(monkeypatch, tmp_path, capsys, host, expected_host):
    from urllib.parse import parse_qs, urlsplit

    from fastapi.testclient import TestClient

    from cloverboard.cli import main

    applications = []
    monkeypatch.setattr("uvicorn.run", lambda app, **options: applications.append(app))
    for _ in range(2):
        main(["serve", "--logdir", str(tmp_path), "--share", "--host", host, "--port", "6789"])
        link = capsys.readouterr().out.splitlines()[0].split("URL: ", 1)[1]
        parsed = urlsplit(link)
        assert parsed.hostname == expected_host
        assert parsed.port == 6789
        token = parse_qs(parsed.query)["token"][0]
        client = TestClient(applications[-1])
        assert client.get("/api/runs").status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/workspace", headers=headers).json()["writable"] is False
        assert client.post("/api/workspace/views", headers=headers, json={"name": "x", "state": {}}).status_code == 403
    assert applications[0].state.access_token != applications[1].state.access_token

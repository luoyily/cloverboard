import pytest

from cloverboard.cli import build_parser


def test_serve_defaults():
    args = build_parser().parse_args(["serve"])

    assert args.logdir == "./runs"
    assert args.host == "127.0.0.1"
    assert args.port == 7230
    assert args.read_only is False
    assert args.share is False
    assert args.peers == ""


def test_serve_accepts_peer_share_url(monkeypatch, tmp_path):
    from cloverboard.cli import main

    applications = []
    monkeypatch.setattr("uvicorn.run", lambda app, **options: applications.append(app))
    main(["serve", "--logdir", str(tmp_path), "--peers", "10.0.0.12:7230?token=secret"])
    source, = applications[0].state.reader.sources.values()
    assert source.url == "http://10.0.0.12:7230"
    assert source.token == "secret"


def test_optional_tensorboard_dependency_is_not_loaded_by_sdk_or_cli(tmp_path):
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent('''
        import builtins
        from pathlib import Path
        import sys
        original_import = builtins.__import__
        def without_tensorboard(name, *args, **kwargs):
            if name == "tensorboard" or name.startswith("tensorboard."):
                raise ModuleNotFoundError("TensorBoard intentionally unavailable")
            return original_import(name, *args, **kwargs)
        builtins.__import__ = without_tensorboard
        from cloverboard.cli import build_parser, main
        from cloverboard import init, log
        from cloverboard.logger.api import _shutdown
        root = Path(sys.argv[1])
        assert build_parser().parse_args(["serve"]).command == "serve"
        init(logdir=root / "runs", run_dir="native", capture_hardware=False)
        log({"loss": 1}, step=1)
        _shutdown()
        assert (root / "runs/native/run.sqlite").is_file()
        assert not any(name == "tensorboard" or name.startswith("tensorboard.") for name in sys.modules)
        try:
            main(["import-tensorboard", str(root), "--output", str(root / "output")])
        except SystemExit as error:
            assert "cloverboard[tensorboard]" in str(error), error
        else:
            raise AssertionError("Missing optional dependency must fail clearly")
        assert not (root / "output").exists()
    ''')
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_serve_passes_workspace_location_and_read_only_mode(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from cloverboard.cli import main

    captured = {}

    def capture_app(app, **options):
        captured["app"] = app

    monkeypatch.setattr("uvicorn.run", capture_app)
    main(["serve", "--logdir", str(tmp_path), "--read-only"])
    client = TestClient(captured["app"], base_url="http://127.0.0.1")
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
        client = TestClient(applications[-1], base_url="http://127.0.0.1")
        assert client.get("/api/runs").status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/workspace", headers=headers).json()["writable"] is False
        assert client.post("/api/workspace/views", headers=headers, json={"name": "x", "state": {}}).status_code == 403
    assert applications[0].state.access_token != applications[1].state.access_token

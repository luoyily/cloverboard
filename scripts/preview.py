"""Build and serve the shared local preview: python scripts/preview.py."""

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.preview_data import PROFILES, RUNS, prepare_data


@contextmanager
def preview_lock(root: Path):
    root = root.absolute()
    if root.resolve() != root:
        raise RuntimeError("Preview root must not be a link or junction")
    root.mkdir(parents=True, exist_ok=True)
    path = root / "preview.lock"
    if path.resolve() != path:
        raise RuntimeError("Preview lock must not be a link")
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"1")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another preview is using this checkout. Stop it before starting or resetting data.") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def build_frontend(skip: bool):
    web = PROJECT / "web"
    if skip:
        if not (web / "dist" / "index.html").is_file():
            raise RuntimeError("Frontend build is missing. Run without --skip-build.")
        print("Using existing web/dist (--skip-build).", flush=True)
        return
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm or not (web / "node_modules").is_dir():
        raise RuntimeError("Install Node.js and run 'npm ci --prefix web' before starting the preview.")
    subprocess.run([npm, "run", "build"], cwd=web, check=True)


def reserve_ports(port: int) -> list[socket.socket]:
    if not 1024 <= port <= 65533:
        raise ValueError("Choose a base port between 1024 and 65533")
    sockets = []
    try:
        for number in range(port, port + 3):
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(connection)
            if os.name == "nt":
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            connection.bind(("127.0.0.1", number))
            connection.listen(128)
        return sockets
    except OSError as error:
        for connection in sockets:
            connection.close()
        raise RuntimeError(f"Preview ports {port}-{port + 2} are unavailable. Stop the owning service or choose --port.") from error


def seed_workspace(directory: Path):
    from cloverboard.server.models import SavedViewInput, WorkspaceSave, WorkspaceState
    from cloverboard.server.sources import qualify
    from cloverboard.server.workspace import WorkspaceStore

    store = WorkspaceStore(directory / "data" / "main")
    if store.snapshot().state is not None:
        return
    state = WorkspaceState(
        selected_ids=["baseline", "lower-lr", "partial-75", qualify("folder", "trial"), qualify("peer", "trial")],
        refresh_interval=0, collapsed_groups=["diagnostics", "optimizer"], baseline_id="baseline",
        metric_columns=["train/loss", "validation/score"],
        targets={
            "train/loss": {"items": [
                {"id": "goal", "name": "Training goal", "chart_label": "Goal", "value": .1, "direction": "lte"},
            ], "active_id": "goal"},
            "validation/score": {"items": [
                {"id": "small", "name": "Small model", "chart_label": "S", "value": .9, "direction": "gte"},
                {"id": "large", "name": "Large model", "chart_label": "L", "value": .95, "direction": "gte"},
            ], "active_id": "small"},
        },
    )
    view = store.save_view(SavedViewInput(name="Training overview", state=state))
    detail = state.model_copy(update={"collapsed_groups": ["diagnostics", "optimizer", "train"], "expanded_charts": ["validation/score"]})
    store.save_view(SavedViewInput(name="Validation detail", state=detail))
    state.active_view_id = view.view_id
    store.save_state(WorkspaceSave(state=state, saved_at=int(time.time() * 1000)))


def serve_instance(role: str, directory: Path, port: int, peer_token: str | None, listener: socket.socket, ready):
    import uvicorn
    from cloverboard.server.app import create_app
    from cloverboard.server.sources import Source
    from cloverboard.storage.sqlite_backend import SqliteStorageReader

    with (directory / f"{role}.log").open("w", encoding="utf8") as stream, redirect_stdout(stream), redirect_stderr(stream):
        try:
            if role == "peer":
                logdir = directory / "data" / "peer"
                app = create_app(SqliteStorageReader(logdir), logdir=logdir, share=True)
            else:
                logdir = directory / "data" / "main"
                sources = [
                    Source("folder", "Local B", reader=SqliteStorageReader(directory / "data" / "local")),
                    Source("peer", "Peer C", url=f"http://127.0.0.1:{port + 1}", token=peer_token),
                ]
                app = create_app(SqliteStorageReader(logdir), logdir=logdir, sources=sources, share=role == "share")
            ready.send(app.state.access_token)
            ready.close()
            number = port + {"main": 0, "peer": 1, "share": 2}[role]
            config = uvicorn.Config(app, host="127.0.0.1", port=number, log_level="warning", access_log=False)
            uvicorn.Server(config).run(sockets=[listener])
        except KeyboardInterrupt:
            pass
        finally:
            listener.close()
            ready.close()


def wait_ready(process, port: int, token: str | None):
    import httpx

    deadline = time.monotonic() + 20
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=.5, trust_env=False) as client:
        while time.monotonic() < deadline:
            if not process.is_alive():
                raise RuntimeError(f"Preview service on {port} exited. Inspect .preview logs.")
            try:
                response = client.get(f"http://127.0.0.1:{port}/api/runs?local_only=true", headers=headers)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.1)
    raise RuntimeError(f"Preview service on {port} did not become ready. Inspect .preview logs.")


def run_services(directory: Path, port: int, sockets: list[socket.socket], check: bool):
    context = multiprocessing.get_context("spawn")
    children = []
    tokens = {}
    runtime = directory.parent / "runtime.json"
    try:
        for role in ("peer", "main", "share"):
            index = {"main": 0, "peer": 1, "share": 2}[role]
            receive, send = context.Pipe(duplex=False)
            process = context.Process(target=serve_instance, name=f"cloverboard-preview-{role}", args=(role, directory, port, tokens.get("peer"), sockets[index], send))
            children.append(process)
            try:
                process.start()
                send.close()
                if not receive.poll(20):
                    raise RuntimeError(f"{role} preview failed to start; inspect {directory / (role + '.log')}")
                tokens[role] = receive.recv()
            finally:
                receive.close()
                send.close()
            wait_ready(process, port + index, tokens[role])
        urls = {
            "main": f"http://127.0.0.1:{port}/",
            "peer": f"http://127.0.0.1:{port + 1}/?token={tokens['peer']}",
            "share": f"http://127.0.0.1:{port + 2}/?token={tokens['share']}",
        }
        runtime.write_text(json.dumps({"pid": os.getpid(), "profile": directory.name, "urls": urls, "children": [child.pid for child in children]}, indent=2), encoding="utf8")
        print(f"\nPreview: {urls['main']}\nPeer:    {urls['peer']}\nShare:   {urls['share']}\nData:    {directory}\nCtrl+C stops all preview services.\n", flush=True)
        if check:
            import httpx
            with httpx.Client(trust_env=False, timeout=10) as client:
                runs = client.get(urls["main"] + "api/runs").json()
                if len(runs) != len(RUNS):
                    raise RuntimeError(f"Preview aggregation did not return {len(RUNS)} runs")
                if client.get(f"http://127.0.0.1:{port + 2}/api/runs").status_code != 401:
                    raise RuntimeError("Share preview unexpectedly allowed anonymous access")
            print(f"Preview check passed: {len(RUNS)} runs, three sources, share authentication.", flush=True)
            return
        while True:
            failed = next((child for child in children if not child.is_alive()), None)
            if failed:
                raise RuntimeError(f"{failed.name} exited. Inspect preview logs.")
            time.sleep(.3)
    finally:
        for child in children:
            if child.pid is not None and child.is_alive():
                child.terminate()
        for child in children:
            if child.pid is not None:
                child.join(timeout=5)
                if child.is_alive():
                    child.kill()
                    child.join(timeout=5)
        for connection in sockets:
            connection.close()
        runtime.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="default")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--port", type=int, default=6030, help="main port; Peer/share use the next two")
    parser.add_argument("--reset", action="store_true", help="rebuild the selected, owned preview profile, including Notes and Views")
    parser.add_argument("--skip-build", action="store_true", help="explicitly reuse existing web/dist")
    parser.add_argument("--check", action="store_true", help="check all services and then stop")
    args = parser.parse_args(argv)
    try:
        with preview_lock(PROJECT / ".preview"):
            sockets = reserve_ports(args.port)
            try:
                build_frontend(args.skip_build)
                directory = PROJECT / ".preview" / args.profile
                prepare_data(directory, args.profile, args.seed, args.reset)
                seed_workspace(directory)
                run_services(directory, args.port, sockets, args.check)
            finally:
                for connection in sockets:
                    connection.close()
    except KeyboardInterrupt:
        print("\nPreview stopped.", flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, EOFError) as error:
        print(f"Preview failed: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())

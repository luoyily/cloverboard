"""Command line entry point for serving and optional data import."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloverboard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="start the web UI")
    serve.add_argument("--logdir", default="./runs", help="directory holding run folders")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=6006)
    serve.add_argument("--read-only", action="store_true", help="reject all write requests")
    serve.add_argument(
        "--share",
        action="store_true",
        help="generate an access token and force read-only mode",
    )
    serve.add_argument("--peers", default="", help="comma-separated NAME=http://host:port sources; loopback is supported")
    serve.add_argument("--source", action="append", default=[], help="additional local NAME=LOGDIR (repeatable)")
    serve.add_argument("--sources", help="TOML source configuration; fixed keys preserve identity across URL changes")
    from cloverboard.importers.tensorboard.cli import add_parser

    add_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "import-tensorboard":
        from cloverboard.importers.tensorboard.cli import run

        run(args)
        return
    if args.command != "serve":
        return

    import uvicorn

    from cloverboard.server.app import create_app
    from cloverboard.storage.sqlite_backend import SqliteStorageReader
    from cloverboard.server.sources import load_sources

    reader = SqliteStorageReader(args.logdir)
    try:
        sources = load_sources(args.peers, args.source, args.sources)
    except (OSError, ValueError) as error:
        raise SystemExit(f"Invalid sources configuration: {error}") from error
    app = create_app(reader, logdir=args.logdir, read_only=args.read_only, share=args.share, sources=sources, host=args.host)
    if args.share:
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(args.host, args.host)
        if ":" in host:
            host = f"[{host}]"
        print(f"Read-only share URL: http://{host}:{args.port}/?token={app.state.access_token}", flush=True)
        print("For frp, use your public address with the same /?token=… suffix.", flush=True)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

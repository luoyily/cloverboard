"""Command line entry point for serving, migration, and optional data import."""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloverboard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="start the web UI")
    serve.add_argument("--logdir", default="./runs", help="directory holding run folders")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7230)
    serve.add_argument("--read-only", action="store_true", help="reject all write requests")
    serve.add_argument(
        "--share",
        action="store_true",
        help="generate an access token and force read-only mode",
    )
    serve.add_argument("--peers", default="", help="comma-separated [NAME=]host:port[?token=TOKEN] sources; HTTP(S) URLs are supported")
    serve.add_argument("--source", action="append", default=[], help="additional local NAME=LOGDIR (repeatable)")
    serve.add_argument("--sources", help="TOML source configuration; fixed keys preserve identity across URL changes")

    migrate = subparsers.add_parser("migrate", help="upgrade workspace and run databases")
    migrate.add_argument("--logdir", default="./runs", help="directory holding run folders")
    migrate.add_argument("--apply", action="store_true", help="write changes; without it the command is a dry run")
    migrate.add_argument("--backup", help="backup root directory")

    from cloverboard.importers.tensorboard.cli import add_parser

    add_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "migrate":
        from cloverboard.migrations.model import MigrationError
        from cloverboard.migrations.runner import migrate_logdir

        try:
            report = migrate_logdir(args.logdir, apply=args.apply, backup_dir=args.backup)
        except (MigrationError, OSError) as error:
            raise SystemExit(f"Migration failed: {error}") from error
        _print_migration_report(report)
        if not report.ok:
            raise SystemExit(1)
        return
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


def _print_migration_report(report) -> None:
    if not report.plans:
        print("No workspace or run databases found.")
    for plan in report.plans:
        if plan.status == "current":
            continue
        label = "workspace" if plan.kind == "workspace" else f"run {plan.run_id}"
        if plan.status == "migrate":
            print(f"{label}: schema {plan.version} -> {plan.target}")
        else:
            print(f"{label}: {plan.status}: {plan.detail}".rstrip(": "))
    current = sum(plan.status == "current" for plan in report.plans) - len(report.applied)
    pending = sum(plan.status == "migrate" for plan in report.plans)
    if current > 0:
        print(f"{current} database(s) already current.")
    if report.backup_root is not None:
        print(f"Backups: {report.backup_root}")
    if report.applied:
        print(f"Migrated {len(report.applied)} database(s).")
    if report.dry_run and pending:
        print("Dry run: no changes written. Re-run with --apply.")
    for error in report.errors:
        print(f"Error: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()

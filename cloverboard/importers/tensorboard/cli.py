"""CLI registration stays usable without the optional TensorBoard package."""

import argparse
from pathlib import Path
import sqlite3
import time


def positive_interval(value: str) -> float:
    import math

    interval = float(value)
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("interval must be a positive finite number of seconds")
    return interval


def add_parser(subparsers):
    parser = subparsers.add_parser("import-tensorboard", help="import TensorBoard scalars; optionally follow live updates")
    parser.add_argument("source", type=Path, help="TensorBoard root with event files or immediate Run subdirectories")
    parser.add_argument("--output", type=Path, required=True, help="CloverBoard logdir receiving standard Run folders")
    parser.add_argument("--follow", action="store_true", help="keep polling after the initial import; Ctrl+C stops")
    parser.add_argument("--interval", type=positive_interval, default=5.0, help="poll interval in seconds (default: 5)")


def run(args):
    try:
        from cloverboard.importers.tensorboard.importer import TensorBoardImporter
    except ImportError as error:
        raise SystemExit('TensorBoard import requires the optional extra: python -m pip install "cloverboard[tensorboard]"') from error
    try:
        with TensorBoardImporter(args.source, args.output) as importer:
            print(f"CloverBoard logdir: {importer.output}", flush=True)
            first = True
            while True:
                result = importer.poll(allow_incomplete=args.follow)
                if first or result.changed_files:
                    print(f"Runs: {result.runs}; files: {result.files}; read: {result.changed_files}; "
                          f"scalar records: {result.scalars}; upserts: {result.written}; "
                          f"replayed runs: {result.replayed_runs}", flush=True)
                    if result.unsupported:
                        counts = ", ".join(f"{name}={count}" for name, count in sorted(result.unsupported.items()))
                        print(f"Skipped non-scalar summaries: {counts}", flush=True)
                    if result.incomplete_files:
                        print(f"Waiting for incomplete or corrupt tails: {len(result.incomplete_files)} file(s)", flush=True)
                if not args.follow:
                    if not result.files:
                        raise ValueError("No TensorBoard event files found")
                    return
                if first:
                    print(f"Following every {args.interval:g}s; Ctrl+C to stop.", flush=True)
                first = False
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("TensorBoard import stopped.", flush=True)
    except (OSError, ValueError, sqlite3.Error) as error:
        raise SystemExit(f"TensorBoard import failed: {error}") from error

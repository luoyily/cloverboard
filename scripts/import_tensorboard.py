"""Convert static TensorBoard logs into a separate CloverBoard test dataset."""

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from cloverboard.metrics import metric_field
from cloverboard.storage.protocols import RunMeta, ScalarRecord
from cloverboard.storage.sqlite_backend import SqliteStorageWriter


@dataclass
class ImportRun:
    directory: str
    run_id: str
    files: list[Path]
    started_at: float | None = None
    scalars: int = 0
    duplicate_steps: int = 0
    duplicate_tags: Counter = field(default_factory=Counter)
    tags: Counter = field(default_factory=Counter)
    unsupported: Counter = field(default_factory=Counter)
    fingerprints: list[dict] = field(default_factory=list)


def read_events(path: Path):
    """Read a complete, unchanged event file; reject truncated/corrupt tails."""
    from google.protobuf.message import DecodeError
    from tensorboard.backend.event_processing.event_file_loader import RawEventFileLoader
    from tensorboard.compat.proto.event_pb2 import Event

    before = path.stat()
    consumed = 0
    for record in RawEventFileLoader(str(path)).Load():
        # TFRecord framing adds an 8-byte length and two 4-byte CRCs. The
        # TensorBoard loader tolerates unfinished tails; an offline import must
        # report them instead of silently presenting an incomplete conversion.
        consumed += len(record) + 16
        try:
            yield Event.FromString(record)
        except DecodeError as error:
            raise ValueError(f"Invalid event protobuf: {path}") from error
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Event file changed during import: {path}")
    if consumed != before.st_size:
        raise ValueError(f"Truncated or corrupt event file: {path} ({consumed}/{before.st_size} bytes)")


def scalar_value(value):
    kind = value.WhichOneof("value")
    if kind == "simple_value":
        return float(value.simple_value)
    if kind == "tensor" and value.metadata.plugin_data.plugin_name in {"", "scalars"}:
        from tensorboard.util.tensor_util import make_ndarray

        array = make_ndarray(value.tensor)
        if array.size == 1 and array.dtype.kind in "biuf":
            return float(array.reshape(-1)[0])
    return None


def fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def scan_run(source: Path, directory: Path, files: list[Path]) -> ImportRun:
    relative = directory.relative_to(source).as_posix()
    identity = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
    run = ImportRun(relative, f"tb_{identity}", files)
    seen = set()
    for path in files:
        before = fingerprint(path)
        for event in read_events(path):
            if math.isfinite(event.wall_time) and event.wall_time > 0:
                run.started_at = min(run.started_at or event.wall_time, event.wall_time)
            for value in event.summary.value:
                number = scalar_value(value)
                if number is None:
                    run.unsupported[value.WhichOneof("value") or "no_value"] += 1
                    continue
                if not math.isfinite(event.wall_time):
                    raise ValueError(f"Non-finite scalar timestamp: {path}, {value.tag}, step {event.step}")
                run.scalars += 1
                run.tags[value.tag] += 1
                key = (value.tag, event.step)
                if key in seen:
                    run.duplicate_steps += 1
                    run.duplicate_tags[value.tag] += 1
                seen.add(key)
        if fingerprint(path) != before:
            raise ValueError(f"Event file changed during scan: {path}")
        run.fingerprints.append(before)
    return run


def metric_mapping(tags) -> dict[str, str]:
    result, used = {}, set()
    for tag in sorted(tags):
        try:
            candidate = metric_field(tag).metric
        except ValueError:
            candidate = "tensorboard/tag_" + hashlib.sha256(tag.encode("utf-8")).hexdigest()[:16]
        if candidate in used:
            candidate += "~" + hashlib.sha256(tag.encode("utf-8")).hexdigest()[:16]
        while candidate in used:
            candidate += "~"
        result[tag] = candidate
        used.add(candidate)
    return result


def write_run(destination: Path, source: Path, run: ImportRun, mapping: dict[str, str]):
    directory = destination / run.run_id
    directory.mkdir()
    db_path = directory / "run.sqlite"
    writer = SqliteStorageWriter(db_path)
    tags = ["tensorboard-import", "local-test"] + (["empty-log"] if not run.scalars else [])
    try:
        writer.init_run(RunMeta(run.run_id, run.directory if run.directory != "." else source.name,
                               run.started_at or 0, env={"tensorboard_import": {
                                   "source_directory": str(source / run.directory),
                                   "tag_map": {tag: mapping[tag] for tag in run.tags},
                               }}, tags=tags))
        batch, written = [], 0
        for path, expected in zip(run.files, run.fingerprints):
            if fingerprint(path) != expected:
                raise ValueError(f"Event file changed since scan: {path}")
            for event in read_events(path):
                for value in event.summary.value:
                    number = scalar_value(value)
                    if number is None:
                        continue
                    batch.append(ScalarRecord(mapping[value.tag], event.step, event.wall_time, number))
                    written += 1
                    if len(batch) >= 4096:
                        writer.append_scalars(batch)
                        batch.clear()
            if fingerprint(path) != expected:
                raise ValueError(f"Event file changed during conversion: {path}")
        writer.append_scalars(batch)
        if written != run.scalars:
            raise ValueError(f"Scalar count changed for {run.directory}")
    finally:
        writer.flush_and_close()
    connection = sqlite3.connect(db_path)
    try:
        count = connection.execute("SELECT COUNT(*) FROM scalars").fetchone()[0]
        if count != run.scalars - run.duplicate_steps:
            raise ValueError(f"Stored scalar count differs for {run.directory}")
    finally:
        connection.close()


def convert(source: Path, output: Path) -> dict:
    """Convert one event directory per Run; later duplicate steps overwrite.

    Source files must be stable. Output must not exist or overlap the source;
    publish it only after every Run has been converted and checked.
    """
    source, output = source.resolve(), output.resolve()
    if not source.is_dir():
        raise ValueError(f"Source directory does not exist: {source}")
    if output.exists():
        raise ValueError(f"Output already exists; choose a new directory: {output}")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Source and output directories must not overlap")
    directories = {}
    for path in sorted(source.rglob("events.out.tfevents.*"), key=lambda item: item.relative_to(source).as_posix()):
        if path.is_file():
            directories.setdefault(path.parent, []).append(path)
    if not directories:
        raise ValueError(f"No TensorBoard event files found in {source}")
    runs = [scan_run(source, directory, files) for directory, files in directories.items()]
    tags = set().union(*(run.tags.keys() for run in runs))
    mapping = metric_mapping(tags)
    report = {
        "format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source), "logdir": str(output / "runs"),
        "run_count": len(runs), "event_file_count": sum(len(run.files) for run in runs),
        "scalar_count": sum(run.scalars - run.duplicate_steps for run in runs),
        "source_scalar_count": sum(run.scalars for run in runs), "empty_run_count": sum(not run.scalars for run in runs),
        "duplicate_step_count": sum(run.duplicate_steps for run in runs), "tag_map": mapping,
        "notes": ["No import-time downsampling or orphan-step purging.",
                  "Files merged in filename order, with event order preserved inside each file.",
                  "Duplicate tag/step observations overwritten in deterministic file and event order.",
                  "Non-scalar summaries are counted but not imported; original files are unchanged."],
        "runs": [{"directory": run.directory, "run_id": run.run_id, "scalars": run.scalars - run.duplicate_steps, "source_scalars": run.scalars,
                  "duplicate_steps": run.duplicate_steps, "tag_counts": {tag: count - run.duplicate_tags[tag] for tag, count in run.tags.items()}, "source_tag_counts": dict(run.tags),
                  "unsupported": dict(run.unsupported), "files": run.fingerprints} for run in runs],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        (staging / "runs").mkdir()
        for run in runs:
            write_run(staging / "runs", source, run, mapping)
        (staging / "import-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        staging.rename(output)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="TensorBoard root; event subdirectories become separate Runs")
    parser.add_argument("--output", type=Path, default=PROJECT / ".local-data" / "tensorboard", help="new output directory")
    args = parser.parse_args(argv)
    try:
        report = convert(args.source, args.output)
    except ImportError as error:
        raise SystemExit("TensorBoard is required for this script: python -m pip install tensorboard") from error
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(f"Converted {report['run_count']} runs, {report['scalar_count']} scalars from {report['event_file_count']} event files.")
    print(f"Empty runs: {report['empty_run_count']}; overwritten duplicate tag/step observations: {report['duplicate_step_count']}")
    print(f"Logdir: {report['logdir']}")
    print(f"Report: {args.output.resolve() / 'import-report.json'}")
    print(f'Preview: python -m cloverboard.cli serve --logdir "{report["logdir"]}" --port 6040')


if __name__ == "__main__":
    main()

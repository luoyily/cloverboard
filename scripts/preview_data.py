"""Reproducible preview fixtures; ordinary records go through the public SDK."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import numpy as np

from cloverboard.storage.schema import SCHEMA_VERSION

FIXTURE_VERSION = 5
BASE_TIME = 1767225600.0
PROFILES = {"default": 1000, "stress": 1000}


@dataclass(frozen=True)
class PreviewRun:
    source: str
    run_id: str
    name: str
    lr: float
    tau: float
    floor: float
    score: float
    validation_every: int
    progress: float = 1.0


RUNS = (
    PreviewRun("main", "baseline", "baseline-unet", .001, 170, .065, .91, 10),
    PreviewRun("main", "lower-lr", "unet-lower-lr", .0005, 210, .035, .95, 13),
    PreviewRun("local", "trial", "unet-augmented", .0008, 190, .05, .935, 10),
    PreviewRun("peer", "trial", "unet-peer", .0015, 155, .085, .90, 17),
    PreviewRun("main", "partial-75", "unet-partial-75", .0007, 200, .055, .93, 11, .75),
)


def metric_rng(seed: int, run: PreviewRun, metric: str) -> np.random.Generator:
    identity = f"{seed}:{run.source}:{run.run_id}:{metric}".encode()
    return np.random.default_rng(int.from_bytes(hashlib.sha256(identity).digest()[:8], "big"))


def noise(seed: int, run: PreviewRun, metric: str, steps: int) -> np.ndarray:
    rng = metric_rng(seed, run, metric)
    independent = rng.normal(size=steps)
    correlated = np.zeros(steps)
    for index in range(1, steps):
        correlated[index] = .82 * correlated[index - 1] + .3 * independent[index]
    return .6 * independent + .4 * correlated


def scalar_values(seed: int, run: PreviewRun, steps: int) -> dict[str, np.ndarray]:
    x = np.arange(steps)
    decay = np.exp(-x / run.tau)
    loss = decay + run.floor + (.045 * decay + .009) * noise(seed, run, "train/loss", steps)
    rng = metric_rng(seed, run, "spikes")
    for position in rng.choice(np.arange(30, steps - 20), size=5, replace=False):
        loss += rng.uniform(.025, .08) * np.exp(-((x - position) / 2.5) ** 2)
    score = run.score - .65 * np.exp(-x / (run.tau * 1.2))
    if run.source == "peer":
        score -= .04 * np.maximum(0, x / steps - .65) / .35
    residual = .04 * noise(seed, run, "diagnostics/residual", steps)
    if run.source == "peer":
        residual[steps // 2] = np.nan
    return {
        "train/loss": np.maximum(.001, loss),
        "train/accuracy": np.clip(.98 - .65 * decay + .012 * noise(seed, run, "train/accuracy", steps), 0, 1),
        "train/grad_norm": np.maximum(.01, 3 * decay + .25 + .08 * noise(seed, run, "train/grad_norm", steps)),
        "validation/loss": np.maximum(.001, 1.12 * decay + run.floor * 1.7 + .018 * noise(seed, run, "validation/loss", steps)),
        "validation/score": np.clip(score + .009 * noise(seed, run, "validation/score", steps), 0, 1),
        "validation/precision": np.clip(score + .015 + .012 * noise(seed, run, "validation/precision", steps), 0, 1),
        "optimizer/lr": run.lr * .4 ** (x // 300),
        "optimizer/weight_decay": np.full(steps, .0001),
        "diagnostics/residual": residual,
    }


def record_run(logdir: Path, run: PreviewRun, seed: int, steps: int):
    """Run in a child process so normal SDK atexit flush completes before reuse."""
    import cloverboard as cb

    recorded_steps = int(steps * run.progress)
    cb.init(exp_name=run.name, logdir=logdir, capture_hardware=False, workspace_root=PROJECT, tags=["preview", "baseline" if run.run_id == "baseline" else "variant", "partial" if run.progress < 1 else "full-budget"], config={
        "model": "unet", "lr": run.lr, "batch_size": 32,
        "augmentation": run.source == "local", "seed": seed,
        "planned_steps": steps, "recorded_steps": recorded_steps,
    })
    cb.log_config({"optimizer": {"name": "AdamW", "weight_decay": .0001}})
    cb.snapshot_code(root=PROJECT, include=["scripts/preview_data.py", "scripts/README.md"])
    for step in (0, recorded_steps // 2, recorded_steps - 1):
        cb.log_text("diagnostics/summary", f"{run.name}\nStep {step}\nRecorded plain text; line breaks and Unicode are preserved.\n纯文本记录示例。", step)
    cb.log_text("diagnostics/long-text", "Long text preview\n\n" + "Plain text keeps <tags> and **Markdown** unchanged.\n" * 50, recorded_steps - 1)
    values = scalar_values(seed, run, steps)
    for step in range(recorded_steps):
        cb.log({name: float(values[f"train/{name}"][step]) for name in ("loss", "accuracy", "grad_norm")}, step, group="train")
        cb.log({name: float(values[f"optimizer/{name}"][step]) for name in ("lr", "weight_decay")}, step, group="optimizer")
        cb.log({"diagnostics/residual": float(values["diagnostics/residual"][step])}, step)
        if step % run.validation_every == 0 or step == recorded_steps - 1:
            cb.log({f"validation/{name}": float(values[f"validation/{name}"][step]) for name in ("loss", "score", "precision")}, step)
    yy, xx = np.mgrid[0:512, 0:768]
    rng = metric_rng(seed, run, "images")
    for step in (0, recorded_steps // 3, recorded_steps * 2 // 3, recorded_steps - 1):
        for sample in range(2):
            heat = np.exp(-((xx / 768 - .4 - sample * .12) ** 2 + (yy / 512 - .5) ** 2) * 12)
            image = np.stack([heat * .65 + .1, heat * .45 + .15, heat * .4 + .3], axis=-1)
            image += rng.normal(0, .01, image.shape)
            cb.log_image("validation/prediction", np.uint8(np.clip(image, 0, 1) * 255), step, caption=f"Sample {sample + 1} · Step {step}")
    sample_rate = 24000
    audio_time = np.arange(sample_rate * 2) / sample_rate
    for step in (0, recorded_steps - 1):
        for sample in range(2):
            frequency = 220 + sample * 110 + run.lr * 10000
            waveform = .22 * np.sin(2 * np.pi * frequency * audio_time) * np.minimum(1, audio_time * 8) * np.minimum(1, (2 - audio_time) * 8)
            cb.log_audio("validation/generated", waveform.astype(np.float32), step, sample_rate=sample_rate, caption=f"Tone {sample + 1} · Step {step}")


def finish_run(directory: Path, run: PreviewRun, index: int, profile: str):
    path = directory / "run.sqlite"
    recorded_steps = int(PROFILES[profile] * run.progress)
    with sqlite3.connect(path) as connection:
        if connection.execute("SELECT COUNT(*) FROM scalars").fetchone()[0] == 0:
            raise RuntimeError(f"SDK did not produce scalar records for {run.name}")
        images = connection.execute("SELECT * FROM images ORDER BY step, image_id").fetchall()
        if len(images) != 8:
            raise RuntimeError(f"SDK produced {len(images)} of 8 expected images for {run.name}")
        # A synthetic training clock makes elapsed/step readouts useful without waiting for training.
        started = BASE_TIME + index * 90
        connection.execute("UPDATE scalars SET wall_time=? + step * 2.4 + CASE WHEN step >= 600 THEN 180 ELSE 0 END", (started,))
        connection.execute("UPDATE images SET wall_time=? + step * 2.4 + CASE WHEN step >= 600 THEN 180 ELSE 0 END", (started,))
        connection.execute("UPDATE audio SET wall_time=? + step * 2.4 + CASE WHEN step >= 600 THEN 180 ELSE 0 END", (started,))
        connection.execute("UPDATE texts SET wall_time=? + step * 2.4 + CASE WHEN step >= 600 THEN 180 ELSE 0 END", (started,))
        connection.execute("INSERT OR REPLACE INTO meta VALUES ('started_at', ?)", (json.dumps(started),))
        connection.execute("INSERT OR REPLACE INTO meta VALUES ('run_id', ?)", (json.dumps(run.run_id),))
        connection.execute("INSERT OR REPLACE INTO meta VALUES ('time_origin', ?)", (json.dumps(started),))
        note = f"# {run.name}\n\n- Seeded synthetic training data.\n- Recorded {recorded_steps} / {PROFILES[profile]} planned steps ({run.progress:.0%}).\n- Validation recorded every {run.validation_every} steps.\n- Static preview; no live training.\n\n```python\nlr = {run.lr}\n```"
        connection.execute("INSERT INTO run_notes VALUES (1, ?, ?, 1)", (note, started))
        if profile == "stress":
            # Pressure fixtures reuse SDK-produced files; this measures index/pagination load, not disk capacity.
            connection.execute("DELETE FROM images")
            records = []
            for ordinal in range(recorded_steps * 20):
                source = images[ordinal % len(images)]
                step = ordinal // 20
                records.append((f"{index * 100000 + ordinal:032x}", source[1], step, started + step * 2.4, source[4], source[5], source[6], source[7], f"Sample {ordinal % 20 + 1}"))
            connection.executemany("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", records)


def prepare_data(directory: Path, profile: str, seed: int, reset: bool = False) -> dict:
    """Create/reuse only an owned fixture directory; mismatches require explicit reset."""
    if profile not in PROFILES:
        raise ValueError("Unknown preview profile")
    directory = directory.absolute()
    if directory.resolve() != directory:
        raise RuntimeError("Preview data directory must not be a link or junction")
    marker = directory / "manifest.json"
    expected = {"kind": "cloverboard-preview", "fixture_version": FIXTURE_VERSION, "schema_version": SCHEMA_VERSION, "profile": profile, "seed": seed}
    previous = json.loads(marker.read_text(encoding="utf8")) if marker.is_file() else None
    if directory.exists() and any(directory.iterdir()):
        if not previous or previous.get("kind") != expected["kind"]:
            raise RuntimeError("Refusing to replace a directory without a Cloverboard preview manifest")
        if not reset:
            if any(previous.get(key) != value for key, value in expected.items()) or not previous.get("complete"):
                raise RuntimeError("Preview fixture version, profile or seed changed; run again with --reset")
            for run in RUNS:
                if not (directory / "data" / run.source / run.run_id / "run.sqlite").is_file():
                    raise RuntimeError("Preview data is incomplete; run again with --reset")
            return previous
        # Restrict recursive deletion to the verified, marked fixture directory.
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({**expected, "complete": False}, indent=2), encoding="utf8")
    for index, run in enumerate(RUNS):
        logdir = directory / "data" / run.source
        logdir.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--record", str(index), "--logdir", str(logdir), "--seed", str(seed), "--steps", str(PROFILES[profile])], check=True, cwd=PROJECT)
        generated = [path for path in logdir.iterdir() if path.name.startswith("run_") and path.is_dir()]
        if len(generated) != 1:
            raise RuntimeError(f"Expected one SDK run under {logdir}")
        destination = logdir / run.run_id
        # Windows file scanners can briefly hold a newly recorded directory after process exit.
        for attempt in range(8):
            try:
                generated[0].rename(destination)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.1 * 2**attempt)
        finish_run(destination, run, index, profile)
        print(f"Prepared {run.name} ({profile})", flush=True)
    manifest = {**expected, "complete": True, "steps": PROFILES[profile], "runs": [asdict(run) for run in RUNS]}
    marker.write_text(json.dumps(manifest, indent=2), encoding="utf8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=int, choices=range(len(RUNS)), required=True)
    parser.add_argument("--logdir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--steps", type=int, default=1000)
    args = parser.parse_args()
    record_run(args.logdir, RUNS[args.record], args.seed, args.steps)

# 🍀 CloverBoard

> **Early Alpha:** CloverBoard is currently in early preview.

Experiment tracking built for comparison: multi-run charts, targets, baselines,
tags and notes in one place. Bring experiments together across your LAN and
let AI agents inspect them through a built-in MCP endpoint.

<p align="center">
  <img src="readme_asset/screenshot_bright_charts.png" alt="Light theme charts with hover deltas" width="49%">
  <img src="readme_asset/screenshot_dark_table.png" alt="Dark theme table with baseline comparisons" width="49%">
</p>

**Python 3.11+ · Local-first · MCP · [MIT](LICENSE)**

[简体中文](README.zh-CN.md)

## Quick start

### 1. Install

From this repository, use Python 3.11+ and Node.js 20.19+ (20.x) or 22.12+:

```sh
python -m pip install -e "."
npm ci --prefix web
npm run build --prefix web
```

Built wheels include the WebUI and do not require Node.js to run.

### 2. Log from Python

Run this example, then replace the demo values with metrics from your training
loop:

```python
import cloverboard as cb
from PIL import Image

cb.init(exp_name="baseline", logdir="./runs", config={"lr": 1e-3}, tags=["demo"])

for step in range(100):
    cb.log({"loss": 1 / (step + 1), "accuracy": step / 100}, step=step, group="train")

cb.log_image("validation/preview", Image.new("RGB", (64, 64), "steelblue"), step=99)
cb.log_text("validation/summary", "Baseline run completed.", step=99)
```

Run it again with another experiment name or configuration to compare runs.
Logs stay on disk under `./runs`, with a separate directory and SQLite database
for each run. Logging does not require the server to be running.

### 3. Open the board

```sh
cloverboard serve --logdir ./runs
```

Open [http://127.0.0.1:7230](http://127.0.0.1:7230). The WebUI and MCP endpoint
start in the same process.

## Four leaves, one board

Record, compare, aggregate, ask.

| Leaf | What it does | Use it when |
| --- | --- | --- |
| **Record** | Log scalars, configs, hyperparameters, images, audio, text and code snapshots from Python. | You want logging inside an existing training loop. |
| **Compare** | Compare runs with charts, tables, targets, a baseline, tags, notes and saved Views. | You are choosing between learning rates, augmentations, datasets or seeds. |
| **Aggregate** | Combine local directories and LAN peers in one board. | Experiments live across a laptop, workstation and GPU machines. |
| **Ask** | Give an MCP client access to runs, metrics, configs, plots and logged images. | You want an agent to inspect experiments without exporting logs first. |

## What “built for comparison” means

- **Colors you can keep track of.** Runs retain their colors across refreshes
  and as new runs arrive. Reassign selected colors, color by hyperparameter
  family, or use a custom palette. Fix a run's color to preserve it through
  reassignment, palette changes and color-mode changes.
- **Targets, a baseline and deltas at the cursor.** Choose one baseline run
  for the board and set named targets for each metric. Inspect
  `Δ Base` / `Δ Target` while moving through chart steps, and compare the same
  references in Table. **Chart settings → Top K** emphasizes leading curves
  in Charts only.
- **Views that restore how you compare.** Save run selection, filters,
  sorting, the baseline, targets, colors and Chart/Table/Media settings as a
  named View. Zoom, pointer position, pinned readouts and playback position
  remain temporary; theme and sidebar width are browser preferences.

Current viewing configuration saves automatically to the location selected in
**Settings**: **Remote workspace** or **Local · this browser**. Normal access
defaults to Remote; read-only and share access save locally. Views and palettes
use the same location. Settings also provides JSON import/export for current
configuration, Views and palettes, plus **Restore remote settings** in Local
mode.

## Aggregate and share

On a GPU machine, serve its log directory in token-protected, read-only mode:

```sh
cloverboard serve --logdir ./runs --host 0.0.0.0 --share
```

On your own machine, use that machine's LAN address and the token printed by
the server:

```sh
cloverboard serve --logdir ./runs --peers "gpu1=10.0.0.12:7230?token=TOKEN"
```

Separate multiple peers with commas. Add another local directory with
`--source "archive=./older-runs"`. Peer aggregation is one hop, and peer data
is read-only. Restarting a shared server generates a new token.

You can also open the shared board in a browser using the GPU machine's LAN
address and `?token=TOKEN`. Viewers can edit and save their own local viewing
configuration without changing the remote workspace or experiment data.

## Already using TensorBoard? Keep it.

CloverBoard works with its own Python SDK or imports scalars from existing
TensorBoard event files without changing your training code. TensorBoard can
keep running alongside it; the importer writes a CloverBoard log directory
and leaves the source event files unchanged.

Install the optional integration from this repository:

```sh
python -m pip install -e ".[tensorboard]"
```

Terminal 1 — import existing scalars and follow new events:

```sh
cloverboard import-tensorboard ./tb_logs --output ./tb_runs --follow --interval 5
```

Terminal 2 — open the imported runs:

```sh
cloverboard serve --logdir ./tb_runs
```

Only scalars are imported. Omit `--follow` for a one-time import, and keep the
source and output directories separate.

## MCP

Connect an MCP client that supports Streamable HTTP to
`http://127.0.0.1:7230/mcp`. It uses the same server and data sources as the
WebUI; no second process is needed.

| Data | Tools |
| --- | --- |
| Runs and details | `list_runs`, `get_runs` |
| Metrics and series | `list_metrics`, `get_series`, `compare_metrics` |
| Config comparison | `compare_configs` |
| Curve plots | `plot_metric` |
| Logged images | `list_image_names`, `list_images`, `get_images` |

For a shared server, use its address with `/mcp` and pass
`Authorization: Bearer TOKEN` in your client's headers.

All MCP tools are read-only: they cannot execute code, start training or modify
experiments. They query experiment data independently of the browser's current
selection, Views and display settings.

## When it fits

Use CloverBoard for single-machine ablations, small teams with experiments
across several GPU machines, and agent-assisted experiment reviews. Training
orchestration and hosted team collaboration are outside its scope.

## Development and tests

After the Quick start setup, install the development dependencies and run the
checks you need:

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
npm test --prefix web
npm run build --prefix web
```

To build a wheel with the WebUI bundled:

```sh
python -m build
```

## License

Licensed under the [MIT License](LICENSE).

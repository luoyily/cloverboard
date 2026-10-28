# 🍀 CloverBoard

> **Early Alpha:** CloverBoard is currently in early preview.

Local-first experiment tracking built for comparison. Log from Python, compare
multiple runs in charts and tables, bring experiments together across your LAN,
and let AI agents inspect logs through a built-in MCP endpoint.

![CloverBoard charts with hover deltas](https://raw.githubusercontent.com/luoyily/cloverboard/main/readme_asset/screenshot_bright_charts.png)

## Install

```sh
python -m pip install cloverboard
```

Requires Python 3.11+. Prebuilt wheels include the WebUI and do not require
Node.js to run.

## Quick start

Add logging to your training loop:

```python
import cloverboard as cb

cb.init(exp_name="baseline", logdir="./runs", config={"lr": 1e-3})

for step in range(100):
    cb.log({"loss": 1 / (step + 1), "accuracy": step / 100}, step=step, group="train")
```

Start the board:

```sh
cloverboard serve --logdir ./runs
```

Open [http://127.0.0.1:7230](http://127.0.0.1:7230). Run the example again with a
different experiment name or configuration to compare runs. Logs stay on disk
in a separate directory and SQLite database for each run.

## What you can do

- **Record:** log scalars, configs, hyperparameters, images, audio, text and
  code snapshots from Python.
- **Compare:** use multi-run charts, tables, targets, a baseline, hover deltas,
  tags, notes and custom colors.
- **Restore:** save viewing configuration, Views and palettes to the remote
  workspace or this browser. Read-only viewers can save their settings locally.
- **Aggregate:** combine local log directories and LAN peers in one board,
  with token-protected, read-only sharing.
- **Ask:** connect an MCP client to `http://127.0.0.1:7230/mcp` to query runs,
  metrics, configs, plots and logged images. MCP tools are read-only.

Existing TensorBoard users can install `cloverboard[tensorboard]` to import
scalars, including continuously following new events.

[Full documentation and examples](https://github.com/luoyily/cloverboard#readme)
· [简体中文](https://github.com/luoyily/cloverboard/blob/main/README.zh-CN.md)
· [Issues](https://github.com/luoyily/cloverboard/issues)
· [MIT License](https://github.com/luoyily/cloverboard/blob/main/LICENSE)

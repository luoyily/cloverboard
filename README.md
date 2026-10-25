# CloverBoard

A lightweight, local-first experiment tracker. Each run has its own directory
and `run.sqlite`; text is stored in SQLite, while images, audio and code ZIPs are files under that run.
Workspace viewing state and named Views live in `.workspace/workspace.sqlite`.

## Start

Requires Python 3.11+. A built wheel includes the WebUI and needs no Node.js:

```sh
python -m pip install ./dist/cloverboard-0.1.0-py3-none-any.whl
cloverboard serve --logdir ./runs --port 6006
```

For development from this checkout, also install Node.js 20.19+ or 22.12+:

```sh
python -m pip install -e ".[dev]"
npm ci --prefix web
npm run build --prefix web
cloverboard serve --logdir ./runs --port 6006
```

The UI and read-only MCP Streamable HTTP endpoint (`/mcp`) start together.
For a reproducible dataset and three-source preview, see [scripts/README.md](scripts/README.md).

To build a wheel and source archive, run `python -m build` after installing the
`dev` extra. The build uses `npm ci` and builds the WebUI from the locked
dependencies. Both outputs go into `dist/`; building from source requires
Node.js, while installing the wheel does not. Local logs, previews and imported
TensorBoard data are excluded.

## TensorBoard scalar import

Install the optional integration in this checkout, then run the importer and
server in separate terminals:

```sh
python -m pip install -e ".[tensorboard]"
cloverboard import-tensorboard ./tb_logs --output ./clover_runs --follow --interval 5
cloverboard serve --logdir ./clover_runs
```

Omit `--follow` for a single import. Source event files are read only; each
immediate Run subdirectory keeps its name in the output. Event files directly
in the source root use the root's directory name. Nested Runs and name collisions
are rejected. Output is a standard CloverBoard logdir, with no extra `runs/`
level; source and output must not overlap. Only scalars are imported.

Polling checks size, modification time and file identity, then reads appended
records using retained cursors. Duplicate metric/Step records use filename
order, then event order, including after restart. Follow mode waits for incomplete
tails. Restarting replays source files with upserts, preserving config,
environment, Tags, Notes and Web workspace state. Full details and boundaries
are in [the import guide](scripts/README.md#import-local-tensorboard-logs).

## Recording

```python
import cloverboard as cb

cb.init(exp_name="unet-lr-test", config={"lr": 1e-5}, tags=["ablation", "dataset-v2"])
cb.log({"loss": 0.12, "score": 0.91}, step=100, group="validation")
cb.add_tags(["reviewed"])
cb.remove_tags(["reviewed"])
cb.log_image("validation/prediction", image, step=100)
cb.log_audio("validation/generated", waveform, step=100, sample_rate=24000)
cb.log_text("debug/output", "A plain text record.\nLine breaks are preserved.", step=100)
```

To continue a stable experiment directory, pass its immediate subdirectory name:

```python
cb.init(logdir="./clover_runs", run_dir="experiment_a", config={"lr": 1e-5})
```

An absent Run database is initialized; an existing one is reopened for appends.
The original display name, start time and environment remain. Explicit config
keys merge, supplied Tags are added, and repeated metric/Step values overwrite.
Without `run_dir`, each `init()` allocates a new Run. The trainer restores its
checkpoint and supplies Steps. To take over an imported experiment, stop the
importer before writing to the same Run from the SDK. Tags, Notes and Views can
still be edited while importing.

Tags are case-sensitive, single-level strings. A run supports 64 tags, each
1–80 characters. SDK and UI use incremental add/remove operations so unrelated
labels survive concurrent updates. Tags travel with the run database.

`log_text(name, text, step)` accepts only a plain string, up to 256 KiB UTF-8.
The same name/Step overwrites its previous body and timestamp. Empty strings,
Unicode, whitespace and newlines are preserved; HTML/Markdown are displayed
literally. Text uses the existing background writer and creates no media file.

`init(workspace_root=...)` optionally records absolute paths **inside that
directory** relative to it in config, argv (including `--option=/path`) and
snapshot metadata. The root itself becomes `.`; outside paths and already
relative strings stay unchanged. The base is fixed at init, even if cwd later
changes. With the default `None`, recording is unchanged. This does not alter
file access, user config objects, text bodies, captions, Notes or archived code,
and the absolute workspace root is not added to recorded metadata.

```python
cb.init(logdir="runs", workspace_root="/home/alice/project",
        config={"data": "/home/alice/project/data"})  # records data as "data"
```

Audio accepts float NumPy arrays in `[-1, 1]`, int16 arrays, or PCM16 WAV paths.
Arrays use `[samples]` or `[samples, channels]` and require an integer sample rate;
mono/stereo and up to 32 MiB decoded PCM per record are supported. WAV paths use
their header's sample rate; an explicit rate must agree. No automatic
normalization, resampling or FFmpeg dependency. WAV encoding runs on a bounded
background queue; overload skips records with a warning. Normal shutdown flushes
the queue; forcibly killing a process can lose pending records.

## Browsing

- Run `⋯` → **Edit tags**, or edit from Details → Overview. Use **Edit tags for
  selection** for bulk add/remove. Peer and share tags are read-only; attached
  local source directories remain editable.
- The tag filter beside search only hides list rows. Selection stays unchanged.
  Dynamic View Rules support exact tag membership (any/all/none) and tagged/untagged.
- Table → **Table settings** controls metric decimal places (0–8, default 4)
  and the optional Tags column. Recorded metrics and deltas use this formatting;
  registered parameters keep their existing representation. Small nonzero values
  and large values use scientific notation. Sorting and calculations use raw values.
- **Media** contains Images, Audio and Text. All use explicit Steps, Follow latest
  and sampled timelines; Images/Audio also support paging. Fixed Steps use the nearest record at or before
  the requested Step. Audio loads on demand and supports seeking through local
  and authenticated Peer file routes. Auto refresh preserves active playback.
- Text shows one string per run/name/Step, with a short initial preview, full
  text expansion and copying. Name/Step choices are saved independently for
  each media type. `/api/text-names`, `/api/text-timeline` and `/api/text` expose
  discovery, sampled Steps and the selected string; missing text returns null.
- Viewing preferences, filters and formatting are saved in workspace state,
  named Views and shared state links.

## MCP over HTTP

Point an MCP client supporting Streamable HTTP at:

```text
http://127.0.0.1:6006/mcp
```

No second process or MCP-specific launch command is required. For `--share`,
use the same access token as the Web service in an `Authorization: Bearer …`
header. Keep credentials out of the MCP URL. Share and read-only modes allow MCP
query POSTs while continuing to reject REST writes. The endpoint exposes only
read tools; it cannot execute Python/SQL, start training or change experiments.

| Tool | Purpose |
| --- | --- |
| `list_runs` | Name/ID/source/config/tag filtering; 10 newest runs by default, up to 50 per page; time/name sorting. |
| `get_runs` | Up to 20 runs; metadata/config keys by default. Config fields, Notes, environment, snapshot summary and full Tags are opt-in. |
| `list_metrics` | Merged metric paths and recording Run IDs, substring filtering and pagination; no points/statistics. |
| `compare_configs` | Config differences by default, escaped dotted field filtering, pagination and per-run errors. |
| `compare_metrics` | Raw latest or at/before a common Step; optional baseline/targets and historical statistics; paginated rows. |
| `get_series` | 200 raw samples by default, Step range, count and sampling metadata; timestamps opt-in. |
| `plot_metric` | One metric across up to 8 runs, inline PNG and compact metadata; optional tail/Step range and EMA. |
| `list_image_names` | Text-only image categories for one run; counts and first/last Steps; filter and page, 20 by default. |
| `list_images` | Text-only IDs/sizes at latest or at/before a Step; 6 by default, optional bounded captions. |
| `get_images` | Read 1–4 selected image IDs as inline PNGs; bounded resolution and optional original-pixel crop. |

`list_runs.query` accepts typed parameter and tag filters. For example:

```json
{"rules": [{"field": "tags", "operator": "has_all", "value": ["ablation"]}]}
```

`get_runs.config_fields` selects escaped dotted leaf paths or prefixes, e.g.
`["optimizer.lr", "model"]`; these return a flat map. `include=["config"]`
requests nested config, while Notes have `notes_offset`/`notes_limit`. Omitted
large fields are named for focused retries. Snapshots return counts/status/size.

`compare_metrics` returns actual sample Steps; deltas appear only when baseline
or targets are supplied. Historical statistics are opt-in through
`include_stats=["extrema", "first_hit"]`: extrema include their original Steps,
and both statistics cover full raw history independently of the selected Step.
Failures are per row. `compare_configs.values` align with `run_ids`;
`missing_indices` distinguish absent fields from null, and `omitted_indices`
identify values beyond the output budget.

JSON outputs use typed models and a 24,000-character compact JSON ceiling. Large
parameter/environment values share a 12,000-character budget. Use filters, smaller
pages or fewer samples when needed. `get_series` never removes internal gap
markers to enforce its cap: excessive gaps produce a prefix with `truncated`
and `next_step` for continuation. Configured sources remain one-hop. Tools do
not inherit Web selections/settings; Notes and Tags are data, not instructions.

`plot_metric` returns native MCP image content (`image/png`, 1000 × 480), with
compact metadata in `structuredContent` and a text block; it does not return
point/tick arrays. For example:

```json
{"run_ids": ["run_20261025_001", "run_20261025_002"], "metric": "metrics/BSDS100/psnr", "range_mode": "last_20"}
```

Use actual IDs from `list_runs`. `range_mode` is `full` (default), `last_50` or
`last_20`, measured on the union of the requested runs' recorded Step spans.
Alternatively pass inclusive `step_min`/`step_max`; presets and explicit bounds
cannot be combined. Raw values are the default; `smoothing` (0–0.999) reuses the
Web's EMA over full history before cropping and sampling. `max_points` defaults
to 800 per curve, up to 2000, with a few extra extrema allowed. Excessive gaps
produce a per-run error instead of silently omitting part of a curve.

The plot uses linear axes, a white background and an indexed legend outside the
drawing area. Full display labels (up to 200 characters), shown legend text,
colors, counts and per-run errors accompany the image. Empty curves are marked
`[no data]`. Colors are stable for the same comparison set and reuse the Web's
base palette, independently of workspace color slots. Images are rendered in
memory with the existing Pillow dependency; no files are written by the tool.
Fonts are detected locally; set `CLOVERBOARD_PLOT_FONT` to a readable TTF/TTC
path when the server has no suitable font, especially for CJK labels. No fonts
are downloaded. The JSON ceiling applies to metadata, not image bytes.

For training images, use `list_image_names` → `list_images` → `get_images`.
Discovery never opens image files. `list_image_names` takes a single `run_id`,
optional case-insensitive `name` substring and `limit`/`offset` (20 by default,
up to 50). `list_images` requires `run_id` and `name`; it returns the actual
Step, total at that Step, `next_offset` and image IDs/dimensions. Omit `step` for
latest, or request the greatest recorded Step at/before it. Pin the returned
Step for subsequent pages of a live run. Pages default to 6 images, up to 24.
`include=["caption"]` adds at most 200 caption characters and a truncation flag.
Offset is a page position, not a cross-run sample identity.

`get_images` requires explicit IDs; only this tool reads training image pixels:

```json
{"items": [{"run_id": "run_20261025_001", "image_id": "<image_id from list_images>", "crop": [120, 80, 256, 256]}], "max_side": 512}
```

The optional crop is `[x, y, width, height]` in original pixels **after EXIF
orientation**. It must fit completely inside the image and is applied before
shrinking. `max_side` defaults to 512 (128–1024); aspect ratio is preserved and
small images are never enlarged. Up to four items can reference different runs
or different crops of the same image. Output is native PNG ImageContent plus
compact metadata: items remain in request order, and successful `image_index`
values number the returned images from 1. Original/returned dimensions and
requested crop are included. Per-item errors preserve other images; a wholly
failed request returns no pixels and `isError=true`. Paths, URLs and captions
are not repeated in this result.

Web and MCP share a 64 MiB local preview cache, keyed by file version, size and
crop. Peer reads fetch selected originals to preserve crop accuracy, with
normal source authentication, a 64 MiB per-file transfer cap and the existing
16-million-pixel image limit. Decoding/encoding has two shared worker slots.
Everything stays in memory; no new image dependency, HTTP endpoint, collage or
automatic sample matching is introduced.

HTTP protocol tests exercise initialization, tool discovery/calls, validation,
authentication and read-only behavior; they require no real Agent registration.

## Checks

```sh
python -m pytest -q
npm test --prefix web
npm run build --prefix web
```

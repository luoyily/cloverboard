# Shared local preview

## Import local TensorBoard logs

The packaged `import-tensorboard` command imports scalar history and optionally
keeps following a live TensorBoard directory. Install its optional dependency
in the same environment; ordinary SDK, Web and MCP usage does not import it.

```sh
python -m pip install -e ".[tensorboard]"
cloverboard import-tensorboard ./tb_logs --output .local-data/tensorboard --follow --interval 5
cloverboard serve --logdir .local-data/tensorboard --port 6040
```

The importer and server run in separate terminals. Ctrl+C stops following and
releases the output lock. Omit `--follow` for a single pass; it rejects an
incomplete/corrupt tail instead of waiting. `python -m cloverboard.cli` also works
when the console command is unavailable.

Each immediate subdirectory with event files becomes `output/<name>/run.sqlite`.
Files in the source root become `output/<source-name>/run.sqlite`. Nested event
directories and case-insensitive Run name collisions are rejected; choose a
narrower source root instead. Directory links/junctions are skipped. Source and
output must not overlap. Existing output can be reused for the same source;
native Runs with a matching name are rejected rather than overwritten.

Legacy scalars and numeric scalar tensors retain Step and wall time, including
NaN/Inf gaps. There is no import-time sampling or restart-step purging. Files
merge in filename order, then event order; the last duplicate metric/Step wins.
Appending an older file cannot override a duplicate in a later file. Multi-level
metric paths retain their groups and names. Noncanonical tags and the reserved
`tensorboard/tag_` prefix map to `tensorboard/tag_<sha256 of original tag>`,
independent of discovery order. Environment metadata records the initial tag map
and relative source directory; later polls do not rewrite that snapshot. Empty
logs remain visible Runs. Media, histograms, text and lifecycle state are not
imported; skipped summary types are counted in console output.

The initial pass reads history. Further polls scan filenames and fresh file
metadata, then decode only appended records. Unchanged files do not reopen or
parse, and no full-file hash is computed. Replacements, size decreases or
same-size modifications replay only the affected Run. Source-only points remain
in the target when source files are removed. The source is authoritative for
metric/Step keys it still contains. Stat-based monitoring assumes appended
event files; arbitrary same-inode rewrites that also grow a file are outside
this contract. Follow mode reports incomplete/corrupt tails and retries when
the file changes; a permanently corrupt tail needs attention at the source.

Event cursors and duplicate precedence are held in memory. Restarting reads
history again and upserts, preserving Run name, start time, config, environment,
Tags, Notes and workspace Views/settings. New Runs are published after their
first successful pass; existing Runs commit batches, so a failed pass may have
written a valid prefix. Retrying is safe. The output's
`.workspace/tensorboard-import.sqlite` holds the source binding and a process
lock, independently of `workspace.sqlite`; it stores no byte checkpoints. A
crash releases the lock automatically. Run one importer per output.

For native SDK takeover, stop the importer and use
`cb.init(logdir=".local-data/tensorboard", run_dir="<experiment-name>")`.
This keeps the same directory/database. The trainer restores checkpoint/Step;
do not run two scalar writers for that Run. Web Tags, Notes and workspace
edits can coexist with importing.

Generated data belongs in ignored `.local-data/`, outside `cloverboard/` and
`web/`. It is independent of `.preview/default` and its reset operation.

## Start the generated preview

From the repository root, use Python 3.11+ and Node.js 20.19+ or 22.12+:

```sh
python -m pip install -e ".[dev]"
npm ci --prefix web
python scripts/preview.py
```

The entry point imports this checkout, builds `web/dist`, prepares seeded data,
and starts three real servers on loopback. Open the printed **Preview** URL.
It does not open a browser automatically. Ctrl+C closes all three servers.
Scripts use Python subprocesses and work without shell-specific commands.

| Command | Purpose |
| --- | --- |
| `python scripts/preview.py` | Build the current frontend; reuse the default dataset and current viewing state. |
| `python scripts/preview.py --reset` | Rebuild the selected profile, including its Notes and Views. |
| `python scripts/preview.py --seed 73 --reset` | Reproduce a different noise realization. |
| `python scripts/preview.py --profile stress` | Use a separate profile with up to 20,000 image indexes per Run. |
| `python scripts/preview.py --port 6040` | Use ports 6040/6041/6042. |
| `python scripts/preview.py --skip-build` | Explicitly reuse `web/dist` after a build already completed. |
| `python scripts/preview.py --check` | Build, start, check aggregation/authentication, and close every service. |

Default ports are 6030 (main), 6031 (Peer with Token), and 6032 (read-only share).
Occupied ports produce an error; the script never kills their owners. One
preview can own a checkout at a time, including while resetting data.

Generated data, logs and active access URLs stay in ignored `.preview/`:

```text
.preview/
  preview.lock           # OS lock; automatically released on process exit
  runtime.json           # active URLs and PIDs; removed on normal shutdown
  default/               # --profile default
    manifest.json        # fixture/schema version, seed and generation status
    main.log / peer.log / share.log
    data/main/           # three Runs and the workspace SQLite
    data/local/          # additional local directory, one Run
    data/peer/           # one Run with the same ID as the local directory
  stress/                # independent dataset and viewing state
```

Reuse requires the same fixture version, schema version, profile and seed.
After fixture/schema changes or interrupted generation, use `--reset`.
Reset is confined to the marked profile directory and rejects linked roots
and unrecognized directories. Do not point real experiment output here.

## Dataset and recording path

Five Runs have different learning rates, convergence speeds, validation
intervals, and final quality. A stable seed plus Run/metric identity determines
noise independently of Python's hash randomization or other metric streams.
Loss includes independent/correlated noise and occasional spikes; scores are
bounded to [0,1], with a late regression on the Peer Run. One diagnostic series
contains a NaN to verify line breaks. Data is static rather than live training.
`unet-partial-75` records only 750 of 1,000 planned steps (Step 0–749),
including its metrics and images. Config and Notes state its recorded budget;
it is a stopped fixture rather than an actively updating training process.

| Group | Fields |
| --- | --- |
| train | loss, accuracy, grad_norm |
| validation | loss, score, precision |
| optimizer | lr, weight_decay |
| diagnostics | residual |

Ordinary scalars, parameters, tags, PNG images, WAV audio and ZIP snapshots are recorded through
the public SDK in short-lived child processes. Normal SDK shutdown flushes
queued writes. The generator then assigns stable fixture directory IDs, Notes,
and a synthetic training clock so elapsed readouts are useful.
The seed reproduces scalar values and image contents, not UUIDs, ZIP metadata,
environment details or byte-identical SQLite files.

The default dataset has four Runs with 1,000 training steps and one with 750,
and eight real images plus four two-second WAV clips per Run. Tags include
`preview`, `baseline`/`variant`, and `full-budget`/`partial`. Audio is recorded at
Step 0 and the run's final Step, making partial-budget behavior visible.
Stress mode reuses those SDK-produced image
files while bulk-inserting 20 indexes per recorded Step: 20,000 per full Run,
15,000 for the partial Run, and 95,000 total. It exercises catalog queries,
sampling and pagination; it is not a disk-capacity test of unique images.

## Quick visual checks

- Train/validation each have three charts; diagnostics has one small chart.
- Expand/restore a chart. It fills its group's row, preserving order, zoom,
  targets and scale. Reload or reopen a View to check size persistence.
- `Validation detail` opens a saved View with `validation/score` expanded.
- Check Raw/EMA against noisy curves; validation Steps intentionally differ.
- Hover compares each Run's nearest raw sample with the baseline's nearest
  raw sample, using the existing Step column. After Step 749 the partial Run
  has no readout or delta; its last sample is not extended into the future.
- Use Media → Images/Audio, arbitrary Step input, thumbnails, playback and Load more.
- Filter tags beside search; edit tags from a Run menu or for the current selection.
- Table → Table settings changes metric precision without formatting config parameters.
- The main service also exposes `/mcp`; HTTP protocol checks need no Agent registration.
- Open Run details for Notes and Files. Additional local Notes are editable;
  Peer/share Notes are read-only. Snapshot ZIPs come from the current scripts.
- Check the printed Share URL, authentication and browser-local viewing state.

Backend changes require stopping and rerunning the entry point. Frontend
changes require rebuilding and reloading; rerunning the default command builds
the latest source. `--skip-build` is an explicit opt-out. Avoid adding separate
ad hoc fixture generators when a new scenario can extend these shared helpers.

## Repeatable Charts performance measurements

With the current preview already running, collect read-only HTTP, client-creation
and local storage timings:

```sh
python scripts/perf.py --url http://127.0.0.1:6030 --repeats 3 --logdir .preview/default/data/main --output .preview/performance/baseline.json
```

Add `--browser` to automatically measure the real production frontend too:

```sh
python scripts/perf.py --url http://127.0.0.1:6030 --repeats 3 --logdir .preview/default/data/main --output .preview/performance/baseline.json --browser
```

Open the printed `http://127.0.0.1:6045/__perf/` URL once. It runs three fresh
documents for local sources and three for a selection including a Peer, then
saves the results to the same JSON and stops its temporary proxy. No extra npm
or browser automation dependency is needed. Use `--port` if 6045 is occupied.

The loopback proxy forwards reads only, supplies an isolated read-only workspace,
disables auto refresh, fixes selection and chart layout, and blocks API writes.
It preserves recorded parameters and the original workspace. `--max-runs` bounds
the selected Runs (default 5); this does not reduce the server's full run-list query.
Credentials, when required, come from `--token-env ENVIRONMENT_VARIABLE` and are
not written to the report.

The report includes per-endpoint min/median/max, response sizes, HTTP client and
optional SQLite CPU profiles, browser request start/end times, first-curve paint
after two animation frames, last-series JSON time, long tasks and asset sizes.
First-curve detection targets the actual scalar path. It also records the DOM
update time and delay until two animation frames, exposing background-tab frame
throttling instead of treating it as data-loading latency.
The legacy `post_data_paint_ms` field is first paint minus last-series JSON;
negative values mean progressive rendering overlapped remaining requests. It is
not the time until every curve is painted. The probe waits for requests and chart
loading indicators to settle before saving the complete request timeline.
Charts use actual application requests, smoothing and sampling; the harness does
not duplicate frontend loading logic. Failed requests/rounds remain visible and
are excluded from browser latency summaries.

Series timings include both single-series and `/api/scalars-batch` reads. Batch
request rows retain the full metric list, so before/after reports can compare
request counts while keeping the same selected runs and chart count.

These are fresh-document application measurements against a running server,
not a cold OS/disk benchmark. Static resources may be cached between rounds;
proxy and instrumentation overhead are included. Three repeats establish a small
local baseline, not a reliable tail-latency percentile or a fixed CI threshold.
Run before/after comparisons with the same profile, selected-Run cap and environment,
and use distinct output filenames. Browser wait expires after 180 seconds by
default (`--browser-timeout`); Ctrl+C also stops the probe. This script never
starts or resets the main preview.

"""Storage contracts shared by the SDK and the web server.

Concrete backends (SQLite today, binary or columnar formats later) implement
these Protocols; callers must never depend on a specific backend.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable


class NoteConflict(ValueError):
    """The note changed since the editor loaded it."""


class RunNotFound(FileNotFoundError):
    """The requested run has no database in this source."""


@dataclass(frozen=True, slots=True)
class RunMeta:
    """Metadata written once when a run is created."""

    run_id: str
    name: str | None
    started_at: float  # unix timestamp in seconds
    config: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Metadata read back for the run list and comparison table."""

    run_id: str
    name: str | None
    started_at: float
    config: dict[str, Any] = field(default_factory=dict)
    source: str = "local"
    time_origin: float | None = None
    time_origin_kind: Literal["step_zero", "first_sample"] | None = None
    source_label: str | None = None
    source_online: bool = True
    has_notes: bool = False
    note_excerpt: str = ""
    tags: list[str] = field(default_factory=list)
    tags_writable: bool = True
    has_scalars: bool = False
    last_recorded_at: float | None = None


@dataclass(frozen=True, slots=True)
class ScalarRecord:
    """One scalar point to be written by the training process."""

    metric: str
    step: int
    wall_time: float
    value: float


@dataclass(frozen=True, slots=True)
class ScalarPoint:
    """One scalar point read back for charting."""

    step: int
    value: float
    smoothed: float | None = None
    wall_time: float | None = None


@dataclass(frozen=True, slots=True)
class ScalarBounds:
    minimum: float | None = None
    maximum: float | None = None
    positive_minimum: float | None = None


@dataclass(frozen=True, slots=True)
class ScalarSummary:
    latest: ScalarPoint | None = None
    minimum: float | None = None
    maximum: float | None = None
    first_hit: ScalarPoint | None = None


@dataclass(frozen=True, slots=True)
class ImageRecord:
    image_id: str
    name: str
    step: int
    wall_time: float
    path: str
    width: int
    height: int
    format: str
    caption: str = ""

@dataclass(frozen=True, slots=True)
class MediaNameSummary:
    name: str
    count: int
    step_count: int
    first_step: int
    last_step: int


@dataclass(frozen=True, slots=True)
class MediaTimeline:
    steps: list[int] = field(default_factory=list)
    total_steps: int = 0


@dataclass(frozen=True, slots=True)
class ImagePage:
    images: list[ImageRecord] = field(default_factory=list)
    step: int | None = None
    total: int = 0
    next_offset: int | None = None


@dataclass(frozen=True, slots=True)
class AudioRecord:
    audio_id: str
    name: str
    step: int
    wall_time: float
    path: str
    sample_rate: int
    channels: int
    frames: int
    caption: str = ""


@dataclass(frozen=True, slots=True)
class AudioPage:
    audio: list[AudioRecord] = field(default_factory=list)
    step: int | None = None
    total: int = 0
    next_offset: int | None = None


@dataclass(frozen=True, slots=True)
class TextRecord:
    name: str
    step: int
    wall_time: float
    text: str


@dataclass(frozen=True, slots=True)
class RunNote:
    body: str = ""
    updated_at: float | None = None
    revision: int = 0
    writable: bool = True


@dataclass(frozen=True, slots=True)
class RunDetails:
    config: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FileAsset:
    media_type: str
    filename: str
    path: Path | None = None
    url: str | None = None
    token: str | None = None


@runtime_checkable
class StorageWriter(Protocol):
    """Write side used by the training process."""

    def init_run(self, meta: RunMeta) -> None:
        """Create the run record; called once per process."""

    def update_config(self, config_delta: Mapping[str, Any]) -> None:
        """Merge new config keys into the run's config."""

    def append_scalars(self, batch: Sequence[ScalarRecord]) -> None:
        """Persist a batch of scalar points."""

    def flush_and_close(self) -> None:
        """Flush buffered data and release resources; safe to call twice."""

    def append_image(self, image: ImageRecord) -> None:
        """Index an image after its file has been committed."""

    def append_audio(self, audio: AudioRecord) -> None: ...
    def append_text(self, text: TextRecord) -> None: ...
    def update_tags(self, add: Sequence[str], remove: Sequence[str]) -> list[str]: ...


@runtime_checkable
class StorageReader(Protocol):
    """Read side used by HTTP and analysis tools.

    Per-run operations raise RunNotFound for absent runs. Existing runs with
    unrecorded metrics/media return empty results. Storage failures propagate.
    Run IDs are opaque; timestamps use Unix seconds and Step ranges are inclusive.
    """

    def list_runs(self) -> list[RunSummary]:
        """Return every run available in the logdir."""

    def update_run_tags(self, run_id: str, add: Sequence[str], remove: Sequence[str]) -> list[str]: ...
    def audio_names(self, run_id: str) -> list[MediaNameSummary]: ...
    def audio_timeline(self, run_id: str, name: str, max_steps: int = 200) -> MediaTimeline: ...
    def audio_page(self, run_id: str, name: str, step: int | None = None, limit: int = 12, offset: int = 0) -> AudioPage: ...
    def text_names(self, run_id: str) -> list[MediaNameSummary]: ...
    def text_timeline(self, run_id: str, name: str, max_steps: int = 200) -> MediaTimeline: ...
    def get_text(self, run_id: str, name: str, step: int | None = None) -> TextRecord | None: ...
    def scalar_count(self, run_id: str, metric: str, step_min: int | None = None, step_max: int | None = None) -> int: ...
    def scalar_at_step(self, run_id: str, metric: str, step: int) -> ScalarPoint | None: ...

    def image_names(self, run_id: str) -> list[MediaNameSummary]: ...
    def image_timeline(self, run_id: str, name: str, max_steps: int = 200) -> MediaTimeline: ...
    def image_page(self, run_id: str, name: str, step: int | None = None, limit: int = 12, offset: int = 0) -> ImagePage: ...
    def get_run_note(self, run_id: str) -> RunNote: ...
    def save_run_note(self, run_id: str, body: str, revision: int) -> RunNote: ...

    def get_run_details(self, run_id: str) -> RunDetails: ...

    def get_asset(self, run_id: str, asset_id: str, thumbnail: bool = False) -> FileAsset | None: ...

    def get_scalar_series(
        self, run_id: str, metric: str, max_points: int = 800,
        step_min: int | None = None, step_max: int | None = None, smoothing: float = 0,
    ) -> list[ScalarPoint]:
        """Return Step-ordered samples retaining finite extrema and non-finite gaps.

        max_points is an approximate budget; unsafe paths raise UnsafeLogPathError.
        """

    def get_scalar_summary(
        self, run_id: str, metric: str, target: float | None = None, direction: str = "gte",
    ) -> ScalarSummary:
        """Return full-history raw statistics, independent of chart sampling and zoom."""

    def get_scalar_series_batch(
        self, run_id: str, metrics: list[str], max_points: int = 800,
        step_min: int | None = None, step_max: int | None = None, smoothing: float = 0,
    ) -> dict[str, list[ScalarPoint]]:
        """Read several metrics from one run with the same sampling semantics."""

    def list_metrics(self, run_id: str) -> list[str]:
        """Return metric names; unsafe paths raise UnsafeLogPathError."""

    def get_scalar_bounds(self, run_id: str, metric: str, step_min: int | None = None, step_max: int | None = None) -> ScalarBounds:
        """Return finite raw bounds before downsampling within the requested step range."""

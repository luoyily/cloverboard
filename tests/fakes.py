"""In-memory storage implementations used by tests."""

from typing import Any
from dataclasses import replace
import math

from cloverboard.storage.protocols import (
    RunMeta, RunNotFound,
    RunSummary,
    ScalarPoint,
    ScalarBounds,
    ScalarRecord,
    ScalarSummary,
    ImageRecord, AudioPage, TextRecord,
    RunDetails, MediaTimeline, ImagePage, RunNote, NoteConflict,
)


class FakeStorageWriter:
    """Collects writes in memory; satisfies StorageWriter."""

    def __init__(self) -> None:
        self.meta: RunMeta | None = None
        self.config: dict[str, Any] = {}
        self.scalars: list[ScalarRecord] = []
        self.flushed = False
        self.closed = False
        self.images: list[ImageRecord] = []
        self.audio = []
        self.tags = []
        self.texts = {}

    def init_run(self, meta: RunMeta) -> None:
        self.meta = meta
        self.config = dict(meta.config)

    def update_config(self, config_delta: dict[str, Any]) -> None:
        self.config.update(config_delta)

    def append_scalars(self, batch: list[ScalarRecord]) -> None:
        unique = {(record.metric, record.step): record for record in self.scalars + batch}
        self.scalars = list(unique.values())

    def flush_and_close(self) -> None:
        self.flushed = True
        self.closed = True

    def append_image(self, image):
        self.images.append(image)

    def append_audio(self, audio):
        self.audio.append(audio)

    def append_text(self, text):
        self.texts[(text.name, text.step)] = text

    def update_tags(self, add, remove):
        self.tags = sorted((set(self.tags) - set(remove)) | set(add))
        return self.tags


class FakeStorageReader:
    """Serves canned data; satisfies StorageReader."""

    def __init__(
        self,
        runs: list[RunSummary] | None = None,
        series: dict[tuple[str, str], list[ScalarPoint]] | None = None,
    ) -> None:
        self._runs = list(runs or [])
        self._series = dict(series or {})

    def _require_run(self, run_id):
        if not any(run.run_id == run_id for run in self._runs) and not any(key[0] == run_id for key in self._series):
            raise RunNotFound("Run not found")

    def list_runs(self) -> list[RunSummary]:
        result = []
        for run in self._runs:
            points = [p for (run_id, _), series in self._series.items() if run_id == run.run_id for p in series if p.wall_time is not None and math.isfinite(p.wall_time)]
            zero = [p for p in points if p.step == 0]
            origin = (zero or points)[0] if points else None
            run = replace(run, has_scalars=bool(self.list_metrics(run.run_id)), last_recorded_at=max((p.wall_time for p in points), default=None))
            result.append(replace(run, time_origin=origin.wall_time, time_origin_kind="step_zero" if zero else "first_sample") if origin else run)
        return result

    def get_scalar_series(
        self, run_id: str, metric: str, max_points: int = 800,
        step_min: int | None = None, step_max: int | None = None, smoothing: float = 0,
    ) -> list[ScalarPoint]:
        self._require_run(run_id)
        result = []
        previous = None
        for point in self._series.get((run_id, metric), []):
            if not math.isfinite(point.value):
                previous = None
            else:
                previous = point.value if previous is None else smoothing * previous + (1 - smoothing) * point.value
            if (step_min is None or point.step >= step_min) and (step_max is None or point.step <= step_max):
                result.append(ScalarPoint(point.step, point.value, previous, point.wall_time))
        return result

    def get_scalar_series_batch(self, run_id, metrics, max_points=800, step_min=None, step_max=None, smoothing=0):
        return {metric: self.get_scalar_series(run_id, metric, max_points, step_min, step_max, smoothing) for metric in metrics}

    def get_scalar_summary(self, run_id, metric, target=None, direction="gte"):
        self._require_run(run_id)
        points = sorted(self._series.get((run_id, metric), []), key=lambda p: p.step)
        valid = [p for p in points if math.isfinite(p.value)]
        hits = [p for p in valid if target is not None and (p.value >= target if direction == "gte" else p.value <= target)]
        return ScalarSummary(
            points[-1] if points else None,
            min((p.value for p in valid), default=None), max((p.value for p in valid), default=None),
            hits[0] if hits else None,
        )

    def list_metrics(self, run_id: str) -> list[str]:
        self._require_run(run_id)
        return sorted(
            metric for (series_run_id, metric) in self._series if series_run_id == run_id
        )

    def get_scalar_bounds(self, run_id, metric, step_min=None, step_max=None):
        self._require_run(run_id)
        values = [p.value for p in self._series.get((run_id, metric), []) if math.isfinite(p.value) and (step_min is None or p.step >= step_min) and (step_max is None or p.step <= step_max)]
        return ScalarBounds(min(values, default=None), max(values, default=None), min((value for value in values if value > 0), default=None))

    def get_run_details(self, run_id):
        self._require_run(run_id)
        run = next((run for run in self._runs if run.run_id == run_id), None)
        return RunDetails(config=run.config if run else {})

    def image_names(self, run_id):
        self._require_run(run_id)
        return []

    def image_timeline(self, run_id, name, max_steps=200):
        self._require_run(run_id)
        return MediaTimeline()

    def image_page(self, run_id, name, step=None, limit=12, offset=0):
        self._require_run(run_id)
        return ImagePage()

    def get_run_note(self, run_id):
        self._require_run(run_id)
        return getattr(self, "_notes", {}).get(run_id, RunNote())

    def save_run_note(self, run_id, body, revision):
        previous = self.get_run_note(run_id)
        if previous.revision != revision:
            raise NoteConflict("Note changed")
        if not hasattr(self, "_notes"):
            self._notes = {}
        self._notes[run_id] = RunNote(body, 1, revision + 1)
        return self._notes[run_id]

    def get_asset(self, run_id, asset_id, thumbnail=False):
        self._require_run(run_id)
        return None

    def update_run_tags(self, run_id, add, remove):
        for index, run in enumerate(self._runs):
            if run.run_id == run_id:
                tags = sorted((set(run.tags) - set(remove)) | set(add))
                self._runs[index] = replace(run, tags=tags)
                return tags
        raise RunNotFound("Run not found")

    def audio_names(self, run_id):
        self._require_run(run_id)
        return []

    def text_names(self, run_id):
        self._require_run(run_id)
        return []

    def text_timeline(self, run_id, name, max_steps=200):
        self._require_run(run_id)
        return MediaTimeline()

    def get_text(self, run_id, name, step=None):
        self._require_run(run_id)
        return None

    def audio_timeline(self, run_id, name, max_steps=200):
        self._require_run(run_id)
        return MediaTimeline()

    def audio_page(self, run_id, name, step=None, limit=12, offset=0):
        self._require_run(run_id)
        return AudioPage()

    def scalar_count(self, run_id, metric, step_min=None, step_max=None):
        return len(self.get_scalar_series(run_id, metric, step_min=step_min, step_max=step_max))

    def scalar_at_step(self, run_id, metric, step):
        points = self.get_scalar_series(run_id, metric, step_max=step)
        return points[-1] if points else None

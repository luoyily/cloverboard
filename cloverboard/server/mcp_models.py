"""Typed, compact results for read-only experiment analysis."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from cloverboard.storage.protocols import MediaNameSummary


class CompactModel(BaseModel):
    @model_serializer(mode="wrap")
    def omit_unrequested(self, handler):
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


class Result(CompactModel):
    @model_validator(mode="after")
    def output_budget(self):
        if len(self.model_dump_json()) > 24000:
            raise ValueError("Result exceeds the 24,000-character budget; reduce limit, fields or max_points")
        return self


class Failure(CompactModel):
    code: str
    message: str


class RunBrief(CompactModel):
    run_id: str
    name: str | None
    source: str
    source_online: bool
    started_at: float
    last_recorded_at: float | None
    has_scalars: bool
    tags: list[str]
    tags_total: int | None = None
    name_truncated: bool = False


class RunList(Result):
    runs: list[RunBrief]
    total: int
    next_offset: int | None


class TextPage(CompactModel):
    text: str
    total_chars: int
    next_offset: int | None


class RunInfo(RunBrief):
    config_keys: list[str]
    config_key_count: int
    has_notes: bool
    config: dict[str, Any] | None = None
    config_format: Literal["nested", "flat"] | None = None
    config_omitted_fields: list[str] | None = None
    notes: TextPage | None = None
    environment: dict[str, Any] | None = None
    environment_omitted_fields: list[str] | None = None
    snapshot: dict[str, Any] | None = None
    errors: dict[str, Failure] | None = None


class RunError(CompactModel):
    run_id: str
    error: Failure


Crop = tuple[Annotated[int, Field(strict=True, ge=0)], Annotated[int, Field(strict=True, ge=0)],
             Annotated[int, Field(strict=True, ge=1)], Annotated[int, Field(strict=True, ge=1)]]


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=2200)
    image_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    crop: Crop | None = Field(default=None, description="[x,y,width,height] in original pixels after EXIF orientation")


class ImageNames(Result):
    run_id: str
    names: list[MediaNameSummary]
    total: int
    next_offset: int | None
    error: Failure | None = None


class ImageItem(CompactModel):
    image_id: str
    width: int
    height: int
    caption: str | None = None
    caption_truncated: bool = False


class ImageList(Result):
    run_id: str
    name: str
    step: int | None
    images: list[ImageItem]
    total: int
    next_offset: int | None
    error: Failure | None = None


class LoadedImage(CompactModel):
    run_id: str
    image_id: str
    image_index: int
    original_size: tuple[int, int]
    returned_size: tuple[int, int]
    crop: Crop | None = None


class ImageError(CompactModel):
    run_id: str
    image_id: str
    error: Failure
    crop: Crop | None = None


class ImagesRead(Result):
    items: list[LoadedImage | ImageError]


class PlotCurve(CompactModel):
    run_id: str
    label: str
    legend: str
    color: str
    raw_count: int
    plotted_points: int
    gaps: int
    downsampled: bool
    label_truncated: bool = False


class MetricPlot(Result):
    metric: str
    range_mode: Literal["full", "last_50", "last_20"]
    step_min: int | None
    step_max: int | None
    smoothing: float
    width: int
    height: int
    empty: bool
    x_range: tuple[float, float] | None
    y_range: tuple[float, float] | None
    curves: list[PlotCurve]
    errors: list[RunError] | None = None


class RunDetails(Result):
    runs: list[RunInfo | RunError]


class MetricItem(CompactModel):
    metric: str
    run_ids: list[str]


class MetricList(Result):
    metrics: list[MetricItem]
    total: int
    next_offset: int | None
    errors: list[RunError] = Field(default_factory=list)


class ConfigRow(CompactModel):
    field: str
    values: list[Any]
    missing_indices: list[int] = Field(default_factory=list)
    omitted_indices: list[int] = Field(default_factory=list)


class ConfigComparison(Result):
    run_ids: list[str]
    rows: list[ConfigRow]
    total: int
    next_offset: int | None
    errors: list[RunError] = Field(default_factory=list)


class Point(CompactModel):
    step: int
    value: float | None
    wall_time: float | None = None


class MetricRow(CompactModel):
    run_id: str
    metric: str
    point: Point | None
    minimum: Point | None = None
    maximum: Point | None = None
    first_hit: Point | None = None
    delta_baseline: float | None = None
    delta_target: float | None = None
    target_met: bool | None = None


class MetricError(CompactModel):
    run_id: str
    metric: str
    error: Failure


class MetricComparison(Result):
    selection: Literal["latest_per_run", "at_or_before"]
    requested_step: int | None = None
    baseline_id: str | None = None
    baseline_errors: list[MetricError] = Field(default_factory=list)
    statistics_scope: Literal["full_raw_history"] | None = None
    rows: list[MetricRow | MetricError]
    total: int
    next_offset: int | None


class Series(Result):
    run_id: str
    metric: str
    raw_count: int
    returned_count: int
    downsampled: bool
    truncated: bool
    next_step: int | None
    points: list[Point]

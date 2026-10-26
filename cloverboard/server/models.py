"""HTTP response models and persisted viewing settings."""

import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from cloverboard.tags import normalize_tags


class RunSummaryResponse(BaseModel):
    run_id: str
    name: str | None = None
    started_at: float = Field(description="Run creation time, Unix seconds.")
    config: dict[str, Any] = Field(default_factory=dict)
    source: str = "local"
    time_origin: float | None = None
    time_origin_kind: Literal["step_zero", "first_sample"] | None = None
    source_label: str | None = None
    source_online: bool = True
    has_notes: bool = False
    note_excerpt: str = ""
    tags: list[str] = Field(default_factory=list)
    tags_writable: bool = True
    has_scalars: bool = False
    last_recorded_at: float | None = Field(default=None, description="Latest scalar/image/audio recording time, Unix seconds.")


class ScalarPointResponse(BaseModel):
    step: int
    value: float | None = Field(description="Raw value; null represents NaN/Inf and breaks the curve.")
    smoothed: float | None = None
    wall_time: float | None = Field(default=None, description="Recording time, Unix seconds; omitted or null if unavailable.")


class ScalarBoundsResponse(BaseModel):
    minimum: float | None = None
    maximum: float | None = None
    positive_minimum: float | None = None


class ScalarSeriesResponse(BaseModel):
    """Step-ordered samples; smoothed is included only when smoothing is enabled."""

    run_id: str
    metric: str
    points: list[ScalarPointResponse]


class MetricListResponse(BaseModel):
    run_id: str
    metrics: list[str]
    fields: list["MetricFieldResponse"] = Field(default_factory=list)


class MetricFieldResponse(BaseModel):
    metric: str
    group: str
    name: str


class ScalarSummaryResponse(BaseModel):
    """Finite extrema and first target hit over full raw history, independent of sampling."""

    latest: ScalarPointResponse | None = Field(default=None, description="Record with the greatest Step, including a null value if non-finite.")
    minimum: float | None = None
    maximum: float | None = None
    first_hit: ScalarPointResponse | None = None


class ScalarCountResponse(BaseModel):
    count: int


class SourceStatusResponse(BaseModel):
    source_id: str
    label: str
    kind: Literal["local", "peer"]
    online: bool


class TagsResponse(BaseModel):
    tags: list[str]


class ShareTokenResponse(BaseModel):
    token: str | None


class ErrorResponse(BaseModel):
    detail: str | list[dict[str, Any]]


class TargetSettings(BaseModel):
    value: float = Field(allow_inf_nan=False)
    direction: Literal["gte", "lte"] = "gte"


class ChartZoom(BaseModel):
    x_min: float = Field(allow_inf_nan=False)
    x_max: float = Field(allow_inf_nan=False)
    y_min: float | None = Field(default=None, allow_inf_nan=False)
    y_max: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.x_min >= self.x_max:
            raise ValueError("Zoom requires an increasing step range")
        if (self.y_min is None) != (self.y_max is None):
            raise ValueError("Both Y bounds are required")
        if self.y_min is not None and self.y_min >= self.y_max:
            raise ValueError("Zoom requires an increasing value range")
        return self


class RunRule(BaseModel):
    field: str = Field(min_length=1, max_length=500)
    operator: Literal["eq", "neq", "contains", "gt", "gte", "lt", "lte", "exists", "missing", "has_any", "has_all", "has_none", "empty", "not_empty"]
    value: str | bool | int | float | list[str] | None = None

    @model_validator(mode="after")
    def valid_operand(self):
        tag_operators = ("has_any", "has_all", "has_none", "empty", "not_empty")
        if self.field == "tags":
            if self.operator not in tag_operators:
                raise ValueError("Choose a tag membership operator")
            if self.operator in ("has_any", "has_all", "has_none"):
                if not isinstance(self.value, list) or not self.value:
                    raise ValueError("Choose at least one tag")
                self.value = normalize_tags(self.value)
            return self
        if self.operator in tag_operators or isinstance(self.value, list):
            raise ValueError("Tag membership operators require the tags field")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("Rule values must be finite")
        if self.operator in ("gt", "gte", "lt", "lte") and (
            isinstance(self.value, bool) or not isinstance(self.value, (int, float))
        ):
            raise ValueError("Numeric comparisons require a number")
        if self.operator == "contains" and not isinstance(self.value, str):
            raise ValueError("Contains requires text")
        return self


class RunQuery(BaseModel):
    match: Literal["all", "any"] = "all"
    rules: list[RunRule] = Field(default_factory=list, max_length=12)


class TopKSettings(BaseModel):
    enabled: bool = False
    metric: str = ""
    direction: Literal["min", "max"] = "max"
    readout: Literal["latest", "best"] = "latest"
    k: int = Field(default=3, ge=1, le=1000)


class PaletteInput(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=60)
    colors: list[str] = Field(min_length=2, max_length=24)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Palette name cannot be empty")
        return value

    @field_validator("colors")
    @classmethod
    def valid_palette_colors(cls, values):
        if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", value) for value in values):
            raise ValueError("Palette colors must use six-digit hex notation")
        values = [value.lower() for value in values]
        if len(set(values)) != len(values):
            raise ValueError("Palette colors must be unique")
        return values


class CustomPalette(PaletteInput):
    id: str = Field(pattern=r"^custom:[0-9a-f]{32}$")


class BuiltinPaletteSelection(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["builtin"] = "builtin"
    id: Literal["default", "bright"] = "default"


class CustomPaletteSelection(CustomPalette):
    kind: Literal["custom"] = "custom"


class ColorSettings(BaseModel):
    mode: Literal["random", "parameter", "continuous"] = "random"
    palette: Annotated[BuiltinPaletteSelection | CustomPaletteSelection, Field(discriminator="kind")] = Field(default_factory=BuiltinPaletteSelection)
    field: str = ""
    domain: tuple[float, float] = (0, 1)
    overrides: dict[str, str] = Field(default_factory=dict)
    assignments: dict[str, int] = Field(default_factory=dict)
    top_k: TopKSettings = Field(default_factory=TopKSettings)

    @model_validator(mode="after")
    def valid_colors(self):
        if not all(math.isfinite(v) for v in self.domain) or self.domain[0] >= self.domain[1]:
            raise ValueError("Color domain must be finite and increasing")
        if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", v) for v in self.overrides.values()):
            raise ValueError("Colors must use six-digit hex notation")
        if any(v < 0 or v > 100000 for v in self.assignments.values()):
            raise ValueError("Invalid color slot")
        return self


class ColorRegistry(BaseModel):
    version: Literal[1] = 1
    seed: str
    slots: dict[str, int] = Field(default_factory=dict)
    palettes: list[CustomPalette] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def unique_palettes(self):
        if len({palette.id for palette in self.palettes}) != len(self.palettes) or len({palette.name.casefold() for palette in self.palettes}) != len(self.palettes):
            raise ValueError("Palette names and IDs must be unique")
        return self


class ColorAllocation(BaseModel):
    keys: list[str] = Field(max_length=20000)


class WorkspaceState(BaseModel):
    version: Literal[1] = 1
    selected_ids: list[str] | None = None
    search: str = Field(default="", max_length=1000)
    search_regex: bool = False
    run_sort: Literal["started_desc", "started_asc", "name_asc", "name_desc"] = "started_desc"
    metric_filter: str = Field(default="", max_length=1000)
    chart_density: Literal["normal", "compact"] = "normal"
    show_chart_legend: bool = True
    tag_filter: list[str] = Field(default_factory=list, max_length=64)
    tag_filter_match: Literal["any", "all", "none", "empty"] = "any"
    smoothing: float = Field(default=0.6, ge=0, le=0.95, allow_inf_nan=False)
    tooltip_decimals: int = Field(default=3, ge=0, le=8)
    table_decimals: int = Field(default=4, ge=0, le=8)
    show_tags_column: bool = False
    table_time: Literal["last_recorded_at", "started_at", "hidden"] = "last_recorded_at"
    table_show_steps: bool = True
    table_delta: Literal["baseline", "target", "both", "hidden"] = "both"
    tooltip_sort: Literal["value", "list"] = "value"
    tooltip_placement: Literal["vertical", "horizontal"] = "vertical"
    tooltip_time: Literal["hidden", "elapsed", "wall"] = "hidden"
    tooltip_delta: Literal["baseline", "target", "both", "hidden"] = "both"
    chart_warmup: dict[str, int | None] = Field(default_factory=dict)
    y_warmup: int | None = Field(default=None, ge=-(2**53 - 1), le=2**53 - 1)
    y_scale: Literal["linear", "log"] = "linear"
    chart_scales: dict[str, Literal["linear", "log"]] = Field(default_factory=dict)
    collapsed_groups: list[str] = Field(default_factory=list)
    expanded_charts: list[str] = Field(default_factory=list)
    refresh_interval: Literal[0, 5, 10, 30, 60] = 5
    panel: Literal["charts", "table", "images"] = "charts"
    media_type: Literal["images", "audio", "text"] = "images"
    text_name: str = ""
    text_step: int | None = Field(default=None, ge=-(2**53 - 1), le=2**53 - 1)
    audio_name: str = Field(default="", max_length=500)
    audio_step: int | None = Field(default=None, ge=-(2**53 - 1), le=2**53 - 1)
    image_name: str = Field(default="", max_length=500)
    image_step: int | None = Field(default=None, ge=-(2**53 - 1), le=2**53 - 1)
    image_timeline_samples: Literal[50, 100, 200, 500] = 200
    images_per_step: Literal[4, 8, 12, 24] = 12
    differences_only: bool = True
    metric_columns: list[str] | None = None
    hidden_params: list[str] = Field(default_factory=list)
    sort_column: str = "run"
    sort_desc: bool = False
    active_view_id: str | None = None
    baseline_id: str | None = None
    targets: dict[str, TargetSettings] = Field(default_factory=dict)
    chart_zoom: dict[str, ChartZoom] = Field(default_factory=dict)
    colors: ColorSettings = Field(default_factory=ColorSettings)

    @model_validator(mode="after")
    def valid_warmup_steps(self):
        if any(step is not None and abs(step) > (1 << 53) - 1 for step in self.chart_warmup.values()):
            raise ValueError("Warmup step must be a safe integer")
        return self


class WorkspaceSave(BaseModel):
    state: WorkspaceState
    saved_at: int = Field(ge=0)


class SavedViewInput(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")
    state: WorkspaceState


class SavedView(SavedViewInput):
    view_id: str


class ViewRename(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")


class NoteInput(BaseModel):
    body: str = Field(max_length=20000)
    revision: int = Field(ge=0)


class TagUpdate(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=64)
    remove: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("add", "remove")
    @classmethod
    def valid_tags(cls, value):
        return normalize_tags(value)


class WorkspaceSnapshot(BaseModel):
    workspace_id: str
    writable: bool
    state: WorkspaceState | None = None
    saved_at: int = 0
    views: list[SavedView] = Field(default_factory=list)

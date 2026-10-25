"""Small Pillow renderer: independent Step sequences, linear axes, external legend."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

MAX_SERIES = 8


@dataclass(frozen=True)
class PlotSeries:
    label: str
    points: Sequence[tuple[float, float | None]]
    color: str


@dataclass(frozen=True)
class Style:
    width: int = 1000
    height: int = 480
    ss: int = 2
    font_path: str | None = None
    title_size: int = 18
    axis_label_size: int = 12
    tick_size: int = 11
    bg: str = "#ffffff"
    fg: str = "#222222"
    muted: str = "#666666"
    grid: str = "#e9e9e9"
    axis: str = "#b3b3b3"
    line_width: int = 2
    pad: int = 18
    title_gap: int = 12
    tick_len: int = 4
    label_gap: int = 6
    legend_width: int = 240
    legend_gap: int = 16


@dataclass(frozen=True)
class SeriesFacts:
    legend: str
    plotted_points: int
    gaps: int


@dataclass(frozen=True)
class PlotResult:
    image: Image.Image
    series: list[SeriesFacts]
    x_range: tuple[float, float] | None
    y_range: tuple[float, float] | None


@lru_cache(maxsize=32)
def _font(path: str | None, size: int):
    if path:
        return ImageFont.truetype(path, size)
    configured = os.environ.get("CLOVERBOARD_PLOT_FONT")
    if configured:
        return ImageFont.truetype(configured, size)
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = (
        str(windows / "msyh.ttc"), str(windows / "arial.ttf"),
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """Cover the data with roughly 4–6 ticks on a 1/2/2.5/5 grid."""
    if hi < lo:
        lo, hi = hi, lo
    if not math.isfinite(lo) or not math.isfinite(hi) or not math.isfinite(hi - lo):
        lo, hi = 0.0, 1.0
    if hi == lo:
        pad = max(abs(lo) * .01, .5)
        lo, hi = lo - pad, hi + pad
    span = hi - lo
    raw = span / max(1, target - 1)
    exponent = math.floor(math.log10(raw)) if raw > 0 else -323
    best = None
    for exp in (exponent - 1, exponent, exponent + 1):
        for multiplier in (1., 2., 2.5, 5., 10.):
            try:
                step = multiplier * 10. ** exp
            except OverflowError:
                continue
            if not math.isfinite(step) or step <= 0 or not math.isfinite(hi / step) or not math.isfinite(lo / step):
                continue
            start = math.floor(lo / step) * step
            stop = math.ceil(hi / step) * step
            if not math.isfinite(stop - start):
                continue
            count = int(round((stop - start) / step)) + 1
            if not 2 <= count <= 50:
                continue
            coverage = ((lo - start) + (stop - hi)) / span
            score = (0 if 4 <= count <= 6 else 100) + abs(count - target) + .5 * coverage
            ticks = [start + i * step for i in range(count)]
            if len(set(ticks)) != count or not all(math.isfinite(v) for v in ticks):
                continue
            if best is None or score < best[0]:
                best = (score, ticks)
    return best[1] if best else [lo, hi]


def format_ticks(ticks: Sequence[float]) -> list[str]:
    """Keep tick labels distinct, including very small PSNR improvements."""
    if len(ticks) < 2:
        return [f"{v:g}" for v in ticks]
    step = ticks[1] - ticks[0]
    maximum = max(abs(v) for v in ticks)
    cleaned = [0. if abs(v) < abs(step) * 1e-6 else v for v in ticks]
    scientific = maximum >= 1e5 or 0 < maximum < 1e-3
    if not scientific:
        for decimals in range(13):
            rounded = [round(v, decimals) for v in cleaned]
            if len(set(rounded)) == len(ticks) and max(abs(r - v) for r, v in zip(rounded, cleaned)) <= abs(step) * 1e-3:
                return [f"{v:.{decimals}f}" for v in cleaned]
    for precision in range(2, 18):
        labels = [format(v, f".{precision}e" if scientific else f".{precision}g") for v in cleaned]
        if len(set(labels)) == len(ticks):
            return labels
    return [repr(v) for v in cleaned]


@dataclass
class _Prepared:
    segments: list[list[tuple[float, float]]]
    x_range: tuple[float, float] | None
    y_range: tuple[float, float] | None


def _prepare(points: Sequence[tuple[float, float | None]]) -> _Prepared:
    # Storage supplies ordered, unique Steps. Do not sort through a gap or
    # interpolate another run onto this run's recording schedule.
    segments, current = [], []
    for x, y in points:
        if y is None or not math.isfinite(x) or not math.isfinite(y):
            if current:
                segments.append(current)
                current = []
        else:
            current.append((x, y))
    if current:
        segments.append(current)
    finite = [point for segment in segments for point in segment]
    return _Prepared(segments,
        (min(p[0] for p in finite), max(p[0] for p in finite)) if finite else None,
        (min(p[1] for p in finite), max(p[1] for p in finite)) if finite else None)


def _truncate(font, text: str, max_width: float) -> str:
    text = " ".join(text.split())
    if font.getlength(text) <= max_width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        middle = (lo + hi + 1) // 2
        if font.getlength(text[:middle] + "…") <= max_width:
            lo = middle
        else:
            hi = middle - 1
    return text[:lo] + "…"


def render_metric(series: Sequence[PlotSeries], *, title: str = "", x_label: str = "Step",
                  y_label: str = "Value", style: Style = Style()) -> PlotResult:
    """Render at most eight already sampled curves; colors and data belong to the caller.

    Points must use ascending, unique Steps. None/NaN/Inf break a line. The
    renderer never reads storage, resamples data or applies smoothing.
    """
    if len(series) > MAX_SERIES:
        raise ValueError(f"At most {MAX_SERIES} curves are supported")
    if not 1 <= style.ss <= 3 or not 600 <= style.width <= 1600 or not 350 <= style.height <= 900:
        raise ValueError("Plot size or supersampling factor is outside the supported range")
    prepared = [_prepare(curve.points) for curve in series]
    plotted = [curve for curve in prepared if curve.x_range is not None]
    x_range = (min(p.x_range[0] for p in plotted), max(p.x_range[1] for p in plotted)) if plotted else None
    y_range = (min(p.y_range[0] for p in plotted), max(p.y_range[1] for p in plotted)) if plotted else None
    ss = style.ss
    width, height = style.width * ss, style.height * ss
    f_title = _font(style.font_path, style.title_size * ss)
    f_label = _font(style.font_path, style.axis_label_size * ss)
    f_tick = _font(style.font_path, style.tick_size * ss)
    pad, gap, tick = style.pad * ss, style.label_gap * ss, style.tick_len * ss
    def text_height(font):
        box = font.getbbox("Ag")
        return box[3] - box[1] + 4 * ss
    title_h = text_height(f_title) if title else 0
    label_h, tick_h = text_height(f_label), text_height(f_tick)
    xt = nice_ticks(*x_range) if plotted else [0., 1.]
    yt = nice_ticks(*y_range) if plotted else [0., 1.]
    xtl, ytl = format_ticks(xt), format_ticks(yt)
    left = pad + (label_h + gap if y_label else 0) + max(f_tick.getlength(s) for s in ytl) + gap + tick
    top = pad + (title_h + style.title_gap * ss if title else 0)
    right = width - pad - style.legend_width * ss - style.legend_gap * ss
    bottom = height - pad - (label_h + gap if x_label else 0) - tick_h - gap - tick
    if right - left < 80 * ss or bottom - top < 60 * ss:
        raise ValueError("Plot area too small for the requested labels")
    image = Image.new("RGB", (width, height), style.bg)
    draw = ImageDraw.Draw(image)

    def px(value):
        return left + (value - xt[0]) / (xt[-1] - xt[0]) * (right - left)
    def py(value):
        return bottom - (value - yt[0]) / (yt[-1] - yt[0]) * (bottom - top)

    if plotted:
        for value in yt:
            draw.line([(left, py(value)), (right, py(value))], fill=style.grid, width=ss)
        for value in xt:
            draw.line([(px(value), top), (px(value), bottom)], fill=style.grid, width=ss)
        draw.line([(left, top), (left, bottom), (right, bottom)], fill=style.axis, width=ss)
        dot = max(2, style.line_width + 1) * ss
        for curve, data in zip(series, prepared):
            for segment in data.segments:
                if len(segment) == 1:
                    x, y = px(segment[0][0]), py(segment[0][1])
                    draw.ellipse([x - dot, y - dot, x + dot, y + dot], fill=curve.color)
                else:
                    draw.line([(px(x), py(y)) for x, y in segment], fill=curve.color,
                              width=style.line_width * ss, joint="curve")
        last_right = 0.
        for value, label in zip(xt, xtl):
            x, half = px(value), f_tick.getlength(label) / 2
            draw.line([(x, bottom), (x, bottom + tick)], fill=style.axis, width=ss)
            if x - half >= last_right + gap:
                draw.text((x, bottom + tick + gap / 2), label, font=f_tick, fill=style.muted, anchor="mt")
                last_right = x + half
        for value, label in zip(yt, ytl):
            draw.line([(left - tick, py(value)), (left, py(value))], fill=style.axis, width=ss)
            draw.text((left - tick - gap, py(value)), label, font=f_tick, fill=style.muted, anchor="rm")
    else:
        draw.text(((left + right) / 2, (top + bottom) / 2), "No finite data in this range",
                  font=f_label, fill=style.muted, anchor="mm")
    if title:
        draw.text(((left + right) / 2, pad), _truncate(f_title, title, right - left),
                  font=f_title, fill=style.fg, anchor="mt")
    if x_label:
        draw.text(((left + right) / 2, height - pad - label_h), x_label, font=f_label, fill=style.muted, anchor="mt")
    if y_label:
        label = _truncate(f_label, y_label, bottom - top)
        temporary = Image.new("RGBA", (int(f_label.getlength(label)) + 4, label_h + 4), (0, 0, 0, 0))
        ImageDraw.Draw(temporary).text((1, 1), label, font=f_label, fill=style.muted, anchor="lt")
        temporary = temporary.rotate(90, expand=True)
        image.paste(temporary, (pad // 2, int(top + (bottom - top - temporary.height) / 2)), temporary)

    # Index prefixes keep clipped or duplicate names unambiguous in the image.
    legend_x, row_height = right + style.legend_gap * ss, tick_h + 10 * ss
    facts = []
    for index, (curve, data) in enumerate(zip(series, prepared)):
        name = curve.label if data.segments else f"[no data] {curve.label}"
        label = _truncate(f_tick, f"{index + 1}. {name}", (style.legend_width - 22) * ss)
        y = top + index * row_height + row_height / 2
        draw.line([(legend_x, y), (legend_x + 14 * ss, y)], fill=curve.color, width=3 * ss)
        draw.text((legend_x + 20 * ss, y), label, font=f_tick, fill=style.muted, anchor="lm")
        count = sum(len(segment) for segment in data.segments)
        facts.append(SeriesFacts(label, count, max(0, len(data.segments) - 1)))
    if ss > 1:
        image = image.resize((style.width, style.height), Image.Resampling.LANCZOS)
    return PlotResult(image, facts, x_range, y_range)

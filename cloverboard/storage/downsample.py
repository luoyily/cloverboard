"""LTTB downsampling for scalar series.

Non-finite points (NaN/Inf) are always kept so the UI can render line breaks.
The finite remainder is reduced with Largest-Triangle-Three-Buckets, and the
global finite minimum/maximum are force-included so spikes never disappear.
"""

import numpy as np


def select_indices(steps: np.ndarray, values: np.ndarray, max_points: int) -> np.ndarray:
    """Return sorted indices of roughly max_points representative points."""
    steps = np.asarray(steps, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    if len(steps) <= max_points:
        return np.arange(len(steps))

    max_points = max(3, max_points)
    finite_mask = np.isfinite(values)
    finite_indices = np.flatnonzero(finite_mask)
    nonfinite_indices = np.flatnonzero(~finite_mask)

    budget = max(3, max_points - len(nonfinite_indices))
    if len(finite_indices) <= budget:
        return np.sort(np.concatenate([finite_indices, nonfinite_indices]))

    finite_steps = steps[finite_indices]
    finite_values = values[finite_indices]
    chosen = finite_indices[
        _lttb_indices(finite_steps, finite_values, budget)
    ]
    extremes = [
        finite_indices[np.argmin(finite_values)],
        finite_indices[np.argmax(finite_values)],
    ]
    chosen = np.union1d(chosen, extremes)
    chosen = np.union1d(chosen, nonfinite_indices)
    return np.sort(chosen)


def _lttb_indices(x: np.ndarray, y: np.ndarray, threshold: int) -> np.ndarray:
    """Largest-Triangle-Three-Buckets over strictly index-ordered points."""
    count = len(x)
    if threshold >= count or threshold < 3:
        return np.arange(count)

    sampled = np.empty(threshold, dtype=np.int64)
    sampled[0] = 0
    bucket_width = (count - 2) / (threshold - 2)
    anchor = 0

    for i in range(threshold - 2):
        avg_start = int(np.floor((i + 1) * bucket_width)) + 1
        avg_end = int(np.floor((i + 2) * bucket_width)) + 1
        avg_x = x[avg_start:avg_end].mean()
        avg_y = y[avg_start:avg_end].mean()

        range_start = int(np.floor(i * bucket_width)) + 1
        range_end = int(np.floor((i + 1) * bucket_width)) + 1
        candidate_x = x[range_start:range_end]
        candidate_y = y[range_start:range_end]

        area = np.abs(
            (x[anchor] - avg_x) * (candidate_y - y[anchor])
            - (x[anchor] - candidate_x) * (avg_y - y[anchor])
        )
        anchor = range_start + int(np.argmax(area))
        sampled[i + 1] = anchor

    sampled[-1] = count - 1
    return sampled

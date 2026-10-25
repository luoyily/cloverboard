import numpy as np

from cloverboard.storage.downsample import select_indices


def test_short_series_is_returned_unchanged():
    steps = np.arange(10)
    values = np.linspace(1.0, 0.0, 10)

    indices = select_indices(steps, values, max_points=100)

    assert list(indices) == list(range(10))


def test_downsample_keeps_endpoints_spike_and_nan():
    steps = np.arange(1000)
    values = np.sin(steps / 20.0)
    spike_index = 321
    nan_index = 700
    values[spike_index] = 50.0
    values[nan_index] = np.nan

    indices = select_indices(steps, values, max_points=100)

    assert indices[0] == 0
    assert indices[-1] == 999
    assert spike_index in indices
    assert nan_index in indices
    assert len(indices) <= 102
    assert np.all(np.diff(indices) > 0)


def test_downsample_keeps_global_minimum():
    steps = np.arange(5000)
    values = np.linspace(0.0, 1.0, 5000)
    minimum_index = 1234
    values[minimum_index] = -10.0

    indices = select_indices(steps, values, max_points=80)

    assert minimum_index in indices


def test_output_size_respects_budget_without_nan():
    steps = np.arange(10000)
    values = np.random.default_rng(0).random(10000)

    indices = select_indices(steps, values, max_points=500)

    assert len(indices) <= 502

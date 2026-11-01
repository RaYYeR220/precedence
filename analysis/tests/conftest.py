"""Shared fixtures: small synthetic data sets, in memory and on disk."""

from __future__ import annotations

from pathlib import Path

import pytest

from prec_analysis import simulate


@pytest.fixture(scope="session")
def small_sim() -> simulate.SimData:
    return simulate.generate(simulate.preset_small(5))


@pytest.fixture(scope="session")
def small_dir(tmp_path_factory: pytest.TempPathFactory, small_sim: simulate.SimData) -> Path:
    out = tmp_path_factory.mktemp("small-data")
    simulate.write(small_sim, out)
    return out

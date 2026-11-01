"""Mallows machinery and rule-model selection on simulated data."""

from __future__ import annotations

import numpy as np
import pytest

from prec_analysis.mallows import SizeProfile, expected_distance, log_z, sample_orders, theta_hat_batch
from prec_analysis.validate import run_validation


def _inversions(perm: np.ndarray) -> np.ndarray:
    m = perm.shape[-1]
    i, j = np.triu_indices(m, 1)
    return (perm[..., i] > perm[..., j]).sum(axis=-1)


def test_normaliser_and_expected_distance_agree_with_enumeration() -> None:
    from itertools import permutations

    m, theta = 5, 0.7
    perms = np.array(list(permutations(range(m))))
    d = _inversions(perms)
    z = np.exp(-theta * d).sum()
    assert np.isclose(log_z(theta, m)[0], np.log(z))
    assert np.isclose(expected_distance(theta, m)[0], (d * np.exp(-theta * d)).sum() / z)
    assert np.isclose(expected_distance(0.0, m)[0], m * (m - 1) / 4)


def test_sampler_matches_the_model_and_mle_recovers_theta() -> None:
    rng = np.random.default_rng(3)
    for theta in (0.0, 0.3, 1.2):
        samples = sample_orders(rng, theta, (4_000,), 8)
        assert all(sorted(row) == list(range(8)) for row in samples[:50])
        d = _inversions(samples)
        assert abs(d.mean() - expected_distance(theta, 8)[0]) < 0.15
        prof = SizeProfile(np.full(4_000, 8))
        est = prof.theta_exact(float(d.sum()))
        assert abs(est - theta) < 0.05
        batch = theta_hat_batch(np.array([[4_000.0]]), np.array([8]), np.array([float(d.sum())]))[0]
        assert abs(batch - est) < 0.01


@pytest.fixture(scope="module")
def validation() -> dict:
    # Realistic sizes: 6-12 windows per validator per kind, 8-12 probes, 70-95% landing.
    return run_validation(seed=11, reps=3, n_perm=999, n_boot=100)


def test_model_selection_recovers_the_generating_rule(validation: dict) -> None:
    shipped = validation["designs"]["shipped"]
    rejected = validation["designs"]["rejected_falling_sweep"]
    sizes = shipped["sizes"]
    assert 6 <= sizes["windows_per_kind_min"] and sizes["windows_per_kind_max"] <= 12
    assert 0.70 <= sizes["landing_rate_mean"] <= 0.95
    # With fee rising along the sweep every rule is identifiable and must be recovered.
    assert shipped["n_identifiable"] == shipped["n_validators"]
    assert shipped["accuracy"] >= 0.90
    # Falling sweep: every rule the design can tell apart must be recovered...
    assert rejected["accuracy_identifiable"] >= 0.90
    # ...and the only rule it cannot always tell apart is the batch auction.
    assert set(validation["not_identifiable_under_design"]["rejected_falling_sweep"]) <= {"batched"}


def test_threads_show_up_as_an_arm_contrast(validation: dict) -> None:
    for design in validation["designs"].values():
        assert design["threads_arm_contrast_detected"] >= 0.6
        assert design["arm_contrast_false_positive"] <= 0.2


def test_random_validators_get_no_rule(validation: dict) -> None:
    for design in validation["designs"].values():
        assert design["confusion"]["random"]["random"] == design["per_rule"]["random"]["n"]

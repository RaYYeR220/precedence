"""Simulation-based power: the weakest rule each unit's data could have detected.

For a unit (validator or family) with its observed batch-block sizes and
design strata, data sets are drawn from Mallows(θ) around the unit's
reference rule (one θ in every stratum) and put through the same test as the
real data: the AIC gap to `random` against the unit's own permutation null.
Power at θ is the share of data sets in which a rule is declared; the minimum
detectable effect is the smallest θ on the grid with power ≥ 80%, reported
with the concordance it implies.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .constants import ALPHA, POWER_SIMS, POWER_TARGET, POWER_THETAS
from .models import (
    Block,
    ModelResults,
    UnitFit,
    UnitProfile,
    aic_gap,
    candidate_params,
    candidate_ranks,
    seed_for,
    simulate_block,
    unit_candidates,
)
from .orders import OrderSet


def power_curve(
    os: OrderSet,
    signs: np.ndarray,
    strata: np.ndarray,
    n_strata: int,
    fit: UnitFit,
    blocks: list[Block],
    thetas: tuple[float, ...] = POWER_THETAS,
    n_sim: int = POWER_SIMS,
) -> dict[str, Any]:
    """Power of the rule-vs-random test at each θ, and the minimum detectable effect."""
    blocks = [b for b in blocks if b.n_pairs > 0]
    if fit.verdict == "insufficient_data" or not blocks or fit.t_null.size == 0:
        return {"theta_mde": None, "concordance_mde": None, "reference_model": fit.reference_model, "curve": []}
    prof = UnitProfile.build(os, strata, n_strata, blocks)
    null = np.sort(fit.t_null)
    ranks = candidate_ranks(os, blocks, fit.best_index)
    k_rule = candidate_params(len(blocks))
    rng = np.random.default_rng(seed_for("power", fit.label))
    curve = []
    mde = None
    for theta in thetas:
        th = np.full(n_strata, theta)
        sims = [simulate_block(os, signs, strata, n_strata, b, rk, th, n_sim, rng) for b, rk in zip(blocks, ranks)]
        t, _, _ = aic_gap(unit_candidates(sims), k_rule, prof)
        exceed = null.size - np.searchsorted(null, t - 1e-9, side="left")
        p = (1 + exceed) / (1 + null.size)
        power = float(np.mean(p < ALPHA))
        curve.append({"theta": theta, "expected_concordance": float(prof.pooled.expected_concordance(theta)[0]), "power": power})
        if power >= POWER_TARGET:
            mde = curve[-1]
            break
    return {
        "theta_mde": mde["theta"] if mde else None,
        "concordance_mde": mde["expected_concordance"] if mde else None,
        "reference_model": fit.reference_model,
        "n_pairs": fit.n_pairs,
        "n_batch_blocks": fit.n_bb,
        "curve": curve,
        "note": None if mde else f"power stays below {POWER_TARGET:.0%} up to theta {thetas[-1]:g}",
    }


def power_all(os: OrderSet, signs: np.ndarray, results: ModelResults, n_sim: int = POWER_SIMS) -> dict[str, Any]:
    """Minimum detectable effect for every validator and family."""
    n_strata = len(results.strata_names)
    validators = {
        v: power_curve(os, signs, results.strata, n_strata, fit, [results.validator_blocks[v]], n_sim=n_sim)
        for v, fit in sorted(results.validators.items())
    }
    families = {
        fam: power_curve(os, signs, results.strata, n_strata, fit, results.family_blocks[fam], n_sim=n_sim)
        for fam, fit in sorted(results.families.items())
    }
    return {
        "method": (
            f"{n_sim} Mallows data sets per theta (same theta in every stratum) around each unit's reference rule, "
            f"observed batch-block sizes, same permutation null as the real data; "
            f"MDE = smallest theta with power >= {POWER_TARGET:.0%} at alpha {ALPHA}"
        ),
        "validators": validators,
        "families": families,
    }

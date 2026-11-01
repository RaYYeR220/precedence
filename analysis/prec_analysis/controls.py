"""Controls: the pipeline must find nothing where there is nothing.

* Shuffled positions: every batch-block's positions are permuted at random;
  mean τ must be ≈ 0 and rule selection must land on `random`.
* Inert variable: the probe index `idx` carries no information in ladder, tie
  and format batches (fee and send order are permuted independently of it),
  so idx parity vs position must show nothing. Sweep batches are excluded:
  there idx equals the send rank.
* Whole-block observational correlation per family (supplementary): what an
  analysis of all transactions in the same blocks, without our probes, sees.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import binomtest

from .constants import N_BOOT, N_PERM
from .models import fit_all, resample_counts, seed_for
from .orders import OrderSet
from .stats import batch_taus, pair_rate, tau_summary


def shuffled(os: OrderSet, seed_label: str = "shuffle") -> OrderSet:
    """The same batch-blocks with positions permuted uniformly within each."""
    rng = np.random.default_rng(seed_for(seed_label))
    pos = os.column("block_index", np.int64).copy()
    for bb in range(os.n_bb):
        lo, hi = os.item_offset[bb], os.item_offset[bb + 1]
        pos[lo:hi] = rng.permutation(pos[lo:hi])
    return os.with_positions(pos)


N_TAU_SHUFFLES = 200
N_MODEL_SHUFFLES = 3


def _shuffled_tau(os: OrderSet, x_sign: np.ndarray, orient: int, kinds_mask: np.ndarray, label: str) -> dict[str, Any]:
    """Mean per-batch-block τ over many shuffles, and how often a sign test fires."""
    ids = np.flatnonzero(kinds_mask)
    if ids.size == 0:
        return {"n_batch_blocks": 0}
    pairs = os.pairs_of(ids)
    perm = os.permuted_obs(np.random.default_rng(seed_for("control", label)), N_TAU_SHUFFLES, ids)
    sizes = os.sizes
    n0 = sizes * (sizes - 1) / 2.0
    pb = os.pair_bb[pairs]
    xs = x_sign[pairs].astype(float)
    ties = np.bincount(pb, weights=(xs == 0).astype(float), minlength=os.n_bb)
    denom = np.sqrt((n0 - ties) * n0)
    ok = ids[denom[ids] > 0]
    means, fired = [], 0
    for row in perm:
        num = np.bincount(pb, weights=xs * orient * row, minlength=os.n_bb)
        tau = num[ok] / denom[ok]
        means.append(float(tau.mean()))
        pos, neg = int(np.sum(tau > 0)), int(np.sum(tau < 0))
        if pos + neg and binomtest(pos, pos + neg, 0.5).pvalue < 0.05:
            fired += 1
    means_a = np.array(means)
    return {
        "n_batch_blocks": int(ok.size),
        "n_shuffles": N_TAU_SHUFFLES,
        "mean_tau": float(means_a.mean()),
        "range_95": [float(np.quantile(means_a, 0.025)), float(np.quantile(means_a, 0.975))],
        "sign_test_rejection_rate": fired / N_TAU_SHUFFLES,
    }


def shuffled_control(os: OrderSet, signs: np.ndarray, n_perm: int = N_PERM, n_boot: int = N_BOOT) -> dict[str, Any]:
    if os.n_bb == 0:
        return {"n_batch_blocks": 0}
    kinds = os.bbs["kind"].astype(str).to_numpy()
    selected: Counter[str] = Counter()
    fam_selected: Counter[str] = Counter()
    for i in range(N_MODEL_SHUFFLES):
        res = fit_all(shuffled(os, f"shuffle|{i}"), signs, n_perm=n_perm, n_boot=n_boot, salt=f"|shuffled{i}")
        selected.update(f.selected for f in res.validators.values() if f.verdict != "insufficient_data")
        fam_selected.update(f.selected for f in res.families.values() if f.verdict != "insufficient_data")
    n_fit = sum(selected.values())
    n_fam = sum(fam_selected.values())
    return {
        "tau_priority_ladder_format": _shuffled_tau(os, os.key_sign(os.column("agave_priority")), 1, np.isin(kinds, ["ladder", "format"]), "tp"),
        "tau_send_tie_sweep": _shuffled_tau(os, os.key_sign(os.column("t_send_us")), -1, np.isin(kinds, ["tie", "sweep"]), "ts"),
        "model_shuffles": N_MODEL_SHUFFLES,
        "validators_selected": dict(sorted(selected.items())),
        "share_validators_random": (selected.get("random", 0) / n_fit) if n_fit else None,
        "families_selected": dict(sorted(fam_selected.items())),
        "share_families_random": (fam_selected.get("random", 0) / n_fam) if n_fam else None,
        "expected": "mean tau ~ 0, sign test fires ~5% of shuffles, units select random except at the 5% test level",
    }


def inert_control(os: OrderSet, n_boot: int = N_BOOT, n_perm: int = N_PERM) -> dict[str, Any]:
    if os.n_bb == 0:
        return {"n_batch_blocks": 0}
    not_sweep = os.bbs["kind"].astype(str).to_numpy() != "sweep"
    sub = os.subset(not_sweep)
    if sub.n_bb == 0:
        return {"n_batch_blocks": 0}
    taus = batch_taus(sub)
    parity_pred = sub.key_sign(sub.column("idx", np.int64) % 2)
    return {
        "variable": "idx parity (odd before even), ladder/tie/format batches",
        "tau_parity": tau_summary(taus["tau_parity"].to_numpy(), sub.bbs["cluster"].to_numpy(), "control|inert|tau", n_boot),
        "odd_idx_first": pair_rate(sub, parity_pred, "control|inert|rate", n_boot, n_perm),
        "expected": "rate ~ 0.5, tau ~ 0, p_perm uniform",
    }


def _mean_ci(values: np.ndarray, label: str, n_boot: int) -> dict[str, Any]:
    v = values[np.isfinite(values)]
    if v.size == 0:
        return {"n": 0, "mean": None, "ci": [None, None]}
    k = resample_counts(np.random.default_rng(seed_for("block-boot", label)), v.size, n_boot)
    boot = (k @ v) / v.size
    return {"n": int(v.size), "mean": float(v.mean()), "ci": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))]}


def observational_control(blocks_df: pd.DataFrame, n_boot: int = N_BOOT) -> dict[str, Any]:
    if blocks_df.empty:
        return {}
    out: dict[str, Any] = {}
    for fam, grp in blocks_df.groupby("family", sort=True):
        out[str(fam)] = {
            "n_blocks": int(len(grp)),
            "spearman_priority_index": _mean_ci(grp["spearman_priority_index"].to_numpy(dtype=float), f"obs|{fam}|rho", n_boot),
            "adjacent_monotonicity": _mean_ci(grp["adjacent_monotonicity"].to_numpy(dtype=float), f"obs|{fam}|adj", n_boot),
            "share_zero_priority": _mean_ci(grp["share_zero_priority"].to_numpy(dtype=float), f"obs|{fam}|zero", n_boot),
            "n_tx": _mean_ci(grp["n_tx"].to_numpy(dtype=float), f"obs|{fam}|ntx", n_boot),
        }
    return out


def controls(os: OrderSet, signs: np.ndarray, blocks_df: pd.DataFrame, n_perm: int = N_PERM, n_boot: int = N_BOOT) -> dict[str, Any]:
    return {
        "shuffled_positions": shuffled_control(os, signs, n_perm, n_boot),
        "inert_variable": inert_control(os, n_boot, n_perm),
        "whole_block_observational": observational_control(blocks_df, n_boot),
    }


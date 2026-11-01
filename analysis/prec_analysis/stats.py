"""Descriptive statistics of the observed orders with honest uncertainty.

* Kendall τ_b per batch-block: priority vs position (+1 = higher priority
  earlier) and send time vs position (+1 = earlier sent earlier, FIFO).
* Pair rates ("the higher-priority probe came first") with 95% intervals from
  a bootstrap that resamples batches, and two-sided permutation p-values
  against "positions are exchangeable within each batch-block" (rate 0.5).
* Sign tests across batch-blocks (exact binomial).
* Dose-response of the priority rate by how much later the higher-priority
  probe was sent.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import binomtest

from .constants import GAP_BUCKETS_MS, KINDS, N_BOOT, N_PERM
from .models import resample_counts, seed_for
from .orders import OrderSet

# -- per batch-block τ ----------------------------------------------------------


def _tau_b(os: OrderSet, x_sign: np.ndarray, orientation: np.ndarray) -> np.ndarray:
    """τ_b per batch-block from pair signs of x and of the (oriented) position."""
    sizes = os.sizes
    n0 = sizes * (sizes - 1) / 2.0
    num = np.bincount(os.pair_bb, weights=(x_sign * orientation).astype(float), minlength=os.n_bb)
    ties = np.bincount(os.pair_bb, weights=(x_sign == 0).astype(float), minlength=os.n_bb)
    denom = np.sqrt((n0 - ties) * n0)
    with np.errstate(invalid="ignore", divide="ignore"):
        tau = np.where(denom > 0, num / denom, np.nan)
    return tau


def batch_taus(os: OrderSet, obs: np.ndarray | None = None) -> pd.DataFrame:
    """τ_priority, τ_send and τ_parity (an inert control) for every batch-block."""
    obs = os.obs if obs is None else obs
    prio = os.key_sign(os.column("agave_priority"))
    send = os.key_sign(os.column("t_send_us"))
    parity = os.key_sign(os.column("idx", np.int64) % 2)
    frame = os.bbs[["run_id", "batch_id", "window_key", "landed_slot", "producer", "producer_family", "kind", "arm", "path"]].copy()
    frame["size"] = os.sizes
    frame["tau_priority"] = _tau_b(os, prio, obs)
    frame["tau_send"] = _tau_b(os, send, -obs)
    frame["tau_parity"] = _tau_b(os, parity, obs)
    return frame


def tau_summary(values: np.ndarray, clusters: np.ndarray, label: str, n_boot: int = N_BOOT) -> dict[str, Any]:
    """Mean τ with a batch bootstrap interval and an exact sign test."""
    ok = np.isfinite(values)
    v, c = values[ok], clusters[ok]
    if v.size == 0:
        return {"n": 0, "mean": None, "ci": [None, None], "n_positive": 0, "n_negative": 0, "sign_test_p": None}
    codes, inv = np.unique(c, return_inverse=True)
    s_c = np.bincount(inv, weights=v, minlength=codes.size)
    n_c = np.bincount(inv, minlength=codes.size).astype(float)
    k = resample_counts(np.random.default_rng(seed_for("tau", label)), codes.size, n_boot)
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = (k @ s_c) / (k @ n_c)
    pos, neg = int(np.sum(v > 0)), int(np.sum(v < 0))
    p = float(binomtest(pos, pos + neg, 0.5).pvalue) if pos + neg else None
    return {
        "n": int(v.size),
        "mean": float(v.mean()),
        "ci": [float(np.nanquantile(boot, 0.025)), float(np.nanquantile(boot, 0.975))],
        "n_positive": pos,
        "n_negative": neg,
        "sign_test_p": p,
    }


# -- pair rates --------------------------------------------------------------------


def pair_rate(os: OrderSet, pred: np.ndarray, label: str, n_boot: int = N_BOOT, n_perm: int = N_PERM) -> dict[str, Any]:
    """Share of pairs ordered as `pred` says (+1: a before b), over pairs with pred ≠ 0."""
    use = pred != 0
    n = int(use.sum())
    if n == 0:
        return {"n_pairs": 0, "n_batches": 0, "rate": None, "ci": [None, None], "p_perm": None}
    hit = (pred * os.obs == 1) & use
    clusters = os.bbs["cluster"].to_numpy()[os.pair_bb]
    codes, inv = np.unique(clusters[use], return_inverse=True)
    s_c = np.bincount(inv, weights=hit[use].astype(float), minlength=codes.size)
    n_c = np.bincount(inv, minlength=codes.size).astype(float)
    k = resample_counts(np.random.default_rng(seed_for("rate-boot", label)), codes.size, n_boot)
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = (k @ s_c) / (k @ n_c)
    rate = float(hit.sum() / n)
    p_perm = None
    if n_perm > 0:
        bb_used = np.unique(os.pair_bb[use])
        perm = os.permuted_obs(np.random.default_rng(seed_for("rate-perm", label)), n_perm, bb_used)
        sub = os.pairs_of(bb_used)
        pred_sub = pred[sub].astype(np.int8)
        m = pred_sub != 0
        null = ((perm[:, m] * pred_sub[m]) == 1).mean(axis=1)
        p_perm = float((1 + np.sum(np.abs(null - 0.5) >= abs(rate - 0.5) - 1e-12)) / (1 + n_perm))
    return {
        "n_pairs": n,
        "n_batches": int(codes.size),
        "rate": rate,
        "ci": [float(np.nanquantile(boot, 0.025)), float(np.nanquantile(boot, 0.975))],
        "p_perm": p_perm,
    }


def priority_pred(os: OrderSet) -> np.ndarray:
    """+1 where a has the higher priority (a should come first under priority)."""
    return os.key_sign(os.column("agave_priority"))


def fifo_pred(os: OrderSet) -> np.ndarray:
    """+1 where a was sent earlier (a should come first under FIFO)."""
    return -os.key_sign(os.column("t_send_us"))


def hp_send_lag_us(os: OrderSet) -> np.ndarray:
    """How much later the higher-priority probe of each pair was sent (NaN on ties)."""
    p = os.column("agave_priority")
    t = os.column("t_send_us")
    a, b = os.pair_a, os.pair_b
    return np.where(p[a] > p[b], t[a] - t[b], np.where(p[a] < p[b], t[b] - t[a], np.nan))


def bb_mask(os: OrderSet, **conds: Any) -> np.ndarray:
    """Batch-blocks whose columns equal the given values."""
    m = np.ones(os.n_bb, dtype=bool)
    for col, val in conds.items():
        m &= os.bbs[col].astype(str).to_numpy() == str(val)
    return m


def condition_rates(os: OrderSet, by: str = "producer_family", n_boot: int = N_BOOT, n_perm: int = N_PERM) -> dict[str, Any]:
    """Priority and FIFO pair rates for every unit × kind × arm cell (and the unit total)."""
    out: dict[str, Any] = {}
    units = sorted(set(os.bbs[by].astype(str))) if os.n_bb else []
    for unit in units:
        per_unit: dict[str, Any] = {}
        sub_all = os.subset(bb_mask(os, **{by: unit}))
        per_unit["all"] = {
            "higher_priority_first": pair_rate(sub_all, priority_pred(sub_all), f"{by}|{unit}|all|hp", n_boot, n_perm),
            "earlier_sent_first": pair_rate(sub_all, fifo_pred(sub_all), f"{by}|{unit}|all|es", n_boot, n_perm),
        }
        for kind in KINDS:
            for arm in ("distinct", "shared", "all"):
                conds = {by: unit, "kind": kind}
                if arm != "all":
                    conds["arm"] = arm
                m = bb_mask(os, **conds)
                if not m.any():
                    continue
                sub = os.subset(m)
                key = f"{kind}/{arm}"
                per_unit[key] = {
                    "n_batch_blocks": int(sub.n_bb),
                    "higher_priority_first": pair_rate(sub, priority_pred(sub), f"{by}|{unit}|{key}|hp", n_boot, n_perm),
                    "earlier_sent_first": pair_rate(sub, fifo_pred(sub), f"{by}|{unit}|{key}|es", n_boot, n_perm),
                }
        out[unit] = per_unit
    return out


def dose_response(os: OrderSet, by: str = "producer_family", n_boot: int = N_BOOT, n_perm: int = N_PERM) -> dict[str, Any]:
    """Higher-priority-first rate by how much later the higher-priority probe was sent."""
    out: dict[str, Any] = {}
    units = ["all"] + (sorted(set(os.bbs[by].astype(str))) if os.n_bb else [])
    for unit in units:
        sub = os if unit == "all" else os.subset(bb_mask(os, **{by: unit}))
        pred = priority_pred(sub)
        lag_ms = hp_send_lag_us(sub) / 1000.0
        rows = []
        earlier = np.where((pred != 0) & (lag_ms <= 0), pred, 0)
        rows.append({"bucket": "hp_sent_earlier_or_same", **pair_rate(sub, earlier, f"dose|{unit}|earlier", n_boot, n_perm)})
        for lo, hi, name in GAP_BUCKETS_MS:
            low_ok = (lag_ms > 0) if lo == 0 else (lag_ms >= lo)
            sel = (pred != 0) & low_ok & (lag_ms < hi)
            rows.append({"bucket": name, **pair_rate(sub, np.where(sel, pred, 0), f"dose|{unit}|{name}", n_boot, n_perm)})
        out[unit] = rows
    return out


def format_effect(os: OrderSet, by: str = "producer_family", n_boot: int = N_BOOT, n_perm: int = N_PERM) -> dict[str, Any]:
    """Format batches: among equal-fee v1/legacy pairs, how often the v1 probe came first."""
    out: dict[str, Any] = {}
    fmt = os.items["format"].astype(str).to_numpy()
    fee = os.column("priority_fee")
    a, b = os.pair_a, os.pair_b
    same_fee = fee[a] == fee[b]
    v1_a = (fmt[a] == "v1") & (fmt[b] == "legacy")
    v1_b = (fmt[b] == "v1") & (fmt[a] == "legacy")
    is_format = (os.bbs["kind"].astype(str).to_numpy() == "format")[os.pair_bb]
    pred_all = np.where(is_format & same_fee & v1_a, 1, np.where(is_format & same_fee & v1_b, -1, 0)).astype(np.int8)
    units = ["all"] + (sorted(set(os.bbs[by].astype(str))) if os.n_bb else [])
    for unit in units:
        if unit == "all":
            sub, pred = os, pred_all
        else:
            m = bb_mask(os, **{by: unit})
            sub = os.subset(m)
            pred = pred_all[np.isin(os.pair_bb, np.flatnonzero(m))]
        out[unit] = {"v1_first_same_fee": pair_rate(sub, pred, f"format|{unit}", n_boot, n_perm)}
    return out


def tau_tables(os: OrderSet, by: str = "producer_family", n_boot: int = N_BOOT) -> dict[str, Any]:
    """Mean per-batch τ by unit and kind, with intervals and sign tests."""
    taus = batch_taus(os)
    clusters = os.bbs["cluster"].to_numpy()
    out: dict[str, Any] = {}
    for unit in sorted(set(taus[by].astype(str))):
        per: dict[str, Any] = {}
        for kind in KINDS:
            m = (taus[by].astype(str) == unit).to_numpy() & (taus["kind"].astype(str) == kind).to_numpy()
            if not m.any():
                continue
            per[kind] = {
                "tau_priority": tau_summary(taus["tau_priority"].to_numpy()[m], clusters[m], f"{by}|{unit}|{kind}|tp", n_boot),
                "tau_send": tau_summary(taus["tau_send"].to_numpy()[m], clusters[m], f"{by}|{unit}|{kind}|ts", n_boot),
            }
        out[unit] = per
    return out


def key_rates(os: OrderSet, unit_col: str, unit: str, n_boot: int = N_BOOT, n_perm: int = N_PERM) -> dict[str, Any]:
    """The two headline rates of a unit: priority on ladder+format, FIFO on tie."""
    base = bb_mask(os, **{unit_col: unit})
    kinds = os.bbs["kind"].astype(str).to_numpy()
    out = {}
    lf = os.subset(base & np.isin(kinds, ["ladder", "format"]))
    out["higher_priority_first_ladder_format"] = pair_rate(lf, priority_pred(lf), f"key|{unit_col}|{unit}|hp", n_boot, n_perm)
    tie = os.subset(base & (kinds == "tie"))
    out["earlier_sent_first_tie"] = pair_rate(tie, fifo_pred(tie), f"key|{unit_col}|{unit}|tie", n_boot, n_perm)
    return out

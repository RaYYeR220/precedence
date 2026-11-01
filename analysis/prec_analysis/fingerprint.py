"""Fingerprint: can our probes alone tell which component orders a block?

Each window becomes a feature vector computed from our probes only (order
statistics, inclusion, spill, landing slot, entry sizes). A simple classifier
predicts the producer's orderer class (local-greedy, fd-pack, bam, harmonic)
under leave-one-validator-out cross-validation, so no validator is ever
scored by a model that saw it.

The baseline is the same classifier on features any observer can compute
from all transactions of the same blocks (priority-vs-index correlation,
adjacent monotonicity, transaction count, share of zero-priority
transactions). Both numbers are reported next to their chance levels.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .constants import BATCH_W_GRID_MS, CORE_MODELS, KINDS, ORDERER_CLASSES, PHASE_STEPS, orderer_class
from .orders import OrderSet
from .stats import batch_taus

N_CORE_MODELS = len(CORE_MODELS)
BASELINE_FEATURES = ["spearman_priority_index", "adjacent_monotonicity", "n_tx", "share_zero_priority"]


def _window_order_features(os: OrderSet, signs: np.ndarray) -> pd.DataFrame:
    """Per window: pair-weighted τ, concordance with each core rule, batched gain and w."""
    if os.n_bb == 0:
        return pd.DataFrame(columns=["window_key"])
    taus = batch_taus(os)
    taus["pairs"] = taus["size"] * (taus["size"] - 1) / 2
    wrong = (signs != os.obs[None, :]).astype(float)
    windows = os.bbs["window_key"].astype(str).to_numpy()
    codes, inv_bb = np.unique(windows, return_inverse=True)
    inv_pair = inv_bb[os.pair_bb]
    n_w = codes.size
    pairs_w = np.bincount(inv_pair, minlength=n_w).astype(float)
    dist = np.stack([np.bincount(inv_pair, weights=row, minlength=n_w) for row in wrong])  # (models, windows)
    core = dist[:N_CORE_MODELS]
    batched = dist[N_CORE_MODELS:].reshape(len(BATCH_W_GRID_MS), PHASE_STEPS, n_w).min(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        conc = 1 - core / pairs_w
        gain = (core.min(axis=0) - batched.min(axis=0)) / pairs_w
    best_w = np.array(BATCH_W_GRID_MS, dtype=float)[batched.argmin(axis=0)]
    frame = pd.DataFrame({"window_key": codes, "n_pairs": pairs_w})
    for i, name in enumerate(("fifo", "priority_fifo_ties", "priority_lifo_ties")):
        frame[f"conc_{name}"] = conc[i]
    best_core = core.argmin(axis=0)
    for i, name in enumerate(("fifo", "priority_fifo_ties", "priority_lifo_ties")):
        frame[f"best_is_{name}"] = (best_core == i).astype(float)
    frame["batched_gain"] = gain
    frame["batched_best_w_ms"] = np.where(gain > 0, best_w, np.nan)
    agg = taus.assign(
        tp=taus["tau_priority"] * taus["pairs"],
        ts=taus["tau_send"] * taus["pairs"],
        wp=np.where(taus["tau_priority"].notna(), taus["pairs"], 0),
        ws=np.where(taus["tau_send"].notna(), taus["pairs"], 0),
    ).groupby("window_key")[["tp", "ts", "wp", "ws"]].sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        agg["tau_priority"] = np.where(agg["wp"] > 0, agg["tp"] / agg["wp"], np.nan)
        agg["tau_send"] = np.where(agg["ws"] > 0, agg["ts"] / agg["ws"], np.nan)
    frame = frame.merge(agg[["tau_priority", "tau_send"]], left_on="window_key", right_index=True, how="left")
    return frame


def window_features(probes: pd.DataFrame, os: OrderSet, signs: np.ndarray, blocks_df: pd.DataFrame) -> pd.DataFrame:
    """One row per window with a known producer: probe features, baseline features, label."""
    sent = probes[probes["sent"].astype(bool)]
    rows = []
    bfeat = blocks_df.set_index("slot") if not blocks_df.empty else pd.DataFrame()
    for key, grp in sent.groupby("window_key", sort=True):
        landed = grp[grp["in_block"].astype(bool)]
        if landed.empty:
            continue
        producer = landed["producer"].mode().sort_values().iloc[0]
        own = landed[landed["producer"] == producer]
        family = own["producer_family"].mode().sort_values().iloc[0]
        slot = int(own["landed_slot"].mode().sort_values().iloc[0])
        kind = str(grp["kind"].iloc[0])
        row: dict[str, Any] = {
            "window_key": key,
            "producer": producer,
            "family": family,
            "label": None,
            **{f"kind_{k}": float(kind == k) for k in KINDS},
            "arm_shared": float(str(grp["arm"].iloc[0]) == "shared"),
            "path_rpc": float(str(grp["path"].iloc[0]) == "rpc"),
            "inclusion": float(grp["landed"].mean()),
            "spill_rate": float(landed["spilled"].astype(bool).mean()),
            "n_slots": float(landed["landed_slot"].nunique()),
            "mean_slot_in_window": float(landed["slot_in_window"].astype(float).mean()),
            "mean_entry_size": float(landed["entry_size"].astype(float).mean()) if landed["entry_size"].notna().any() else np.nan,
            "share_single_tx_entry": float((landed["entry_size"].astype(float) == 1).mean()) if landed["entry_size"].notna().any() else np.nan,
        }
        if slot in bfeat.index:
            for f in BASELINE_FEATURES:
                row[f"block_{f}"] = float(bfeat.loc[slot, f])
        rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["label"] = frame["family"].map(orderer_class)
    order = _window_order_features(os, signs)
    frame = frame.merge(order, on="window_key", how="left")
    return frame


def _cv(frame: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    data = frame.dropna(subset=["label"])
    classes = [c for c in ORDERER_CLASSES if c in set(data["label"])]
    n_val = data["producer"].nunique()
    base = {"n_rows": int(len(data)), "n_validators": int(n_val), "classes": classes, "features": features}
    if len(classes) < 2 or n_val < 3:
        return {**base, "accuracy": None, "note": "needs at least two classes and three validators"}
    x = data[features].to_numpy(dtype=float)
    y = data["label"].to_numpy()
    groups = data["producer"].to_numpy()
    pred = np.empty(len(y), dtype=object)
    for train, test in LeaveOneGroupOut().split(x, y, groups):
        if len(set(y[train])) < 2:
            pred[test] = y[train][0]
            continue
        model = make_pipeline(
            SimpleImputer(strategy="constant", fill_value=0.0, add_indicator=True, keep_empty_features=True),
            StandardScaler(),
            LogisticRegression(C=1.0, max_iter=5_000),
        )
        model.fit(x[train], y[train])
        pred[test] = model.predict(x[test])
    correct = pred == y
    confusion = {t: {p: int(np.sum((y == t) & (pred == p))) for p in classes} for t in classes}
    recall = {t: float(np.mean(pred[y == t] == t)) for t in classes}
    shares = {c: float(np.mean(y == c)) for c in classes}
    per_val = pd.DataFrame({"producer": groups, "y": y, "pred": pred})
    votes = per_val.groupby("producer").agg(
        y=("y", lambda s: s.mode().sort_values().iloc[0]),
        pred=("pred", lambda s: s.value_counts().sort_index().idxmax()),
    )
    val_shares = votes["y"].value_counts(normalize=True)
    return {
        **base,
        "accuracy": float(correct.mean()),
        "balanced_accuracy": float(np.mean(list(recall.values()))),
        "chance_majority": float(max(shares.values())),
        "chance_uniform": 1.0 / len(classes),
        "class_shares": shares,
        "recall": recall,
        "confusion": confusion,
        "validator_vote_accuracy": float(np.mean(votes["y"] == votes["pred"])),
        "validator_chance_majority": float(val_shares.max()),
    }


PROBE_FEATURES = (
    [f"kind_{k}" for k in KINDS]
    + ["arm_shared", "path_rpc", "inclusion", "spill_rate", "n_slots", "mean_slot_in_window"]
    + ["tau_priority", "tau_send", "conc_fifo", "conc_priority_fifo_ties", "conc_priority_lifo_ties"]
    + ["best_is_fifo", "best_is_priority_fifo_ties", "best_is_priority_lifo_ties", "batched_gain", "batched_best_w_ms"]
    + ["mean_entry_size", "share_single_tx_entry"]
)


# Validator-level aggregates: (feature, kinds it is averaged over).
VALIDATOR_FEATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tau_priority", ("ladder", "format")),
    ("conc_fifo", ("ladder", "format")),
    ("tau_send", ("tie",)),
    ("conc_priority_lifo_ties", ("tie",)),
    ("tau_send", ("sweep",)),
    ("batched_gain", ("sweep", "ladder")),
    ("inclusion", KINDS),
    ("spill_rate", KINDS),
    ("mean_slot_in_window", KINDS),
    ("mean_entry_size", KINDS),
    ("share_single_tx_entry", KINDS),
)


def validator_frame(frame: pd.DataFrame, base_cols: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """One row per validator: its windows' features averaged by kind, plus the arm gap."""
    rows = []
    names = [f"{feat}@{'+'.join(kinds)}" for feat, kinds in VALIDATOR_FEATURES] + ["arm_gap_priority"]
    for producer, grp in frame.groupby("producer", sort=True):
        kind = grp[[f"kind_{k}" for k in KINDS]].idxmax(axis=1).str.removeprefix("kind_")
        row: dict[str, Any] = {"producer": producer, "label": grp["label"].mode().sort_values().iloc[0] if grp["label"].notna().any() else None}
        for (feat, kinds), name in zip(VALIDATOR_FEATURES, names):
            row[name] = float(grp.loc[kind.isin(kinds), feat].mean()) if feat in grp else np.nan
        ladder = grp[kind == "ladder"]
        shared = ladder.loc[ladder["arm_shared"] == 1, "conc_priority_fifo_ties"].mean()
        distinct = ladder.loc[ladder["arm_shared"] == 0, "conc_priority_fifo_ties"].mean()
        row["arm_gap_priority"] = float(shared - distinct)
        for c in base_cols:
            row[c] = float(grp[c].mean())
        rows.append(row)
    return pd.DataFrame(rows), names


def fingerprint(probes: pd.DataFrame, os: OrderSet, signs: np.ndarray, blocks_df: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    """(probe fingerprint result, whole-block baseline result) on the same windows and validators.

    Each holds a per-window classifier (every window scored by a model that
    never saw its validator) and a per-validator classifier on the
    validator's windows averaged by kind (leave-one-out over validators).
    """
    frame = window_features(probes, os, signs, blocks_df)
    if frame.empty:
        empty = {"window_level": {"n_windows": 0, "accuracy": None}, "validator_level": {"n_validators": 0, "accuracy": None}}
        return empty, empty
    base_cols = [f"block_{f}" for f in BASELINE_FEATURES]
    for c in base_cols:
        if c not in frame.columns:
            frame[c] = np.nan
    usable = frame.dropna(subset=["label"])
    usable = usable[usable[base_cols].notna().any(axis=1)]
    method = "multinomial logistic regression (impute 0 + missing indicators, standardise), leave-one-validator-out"
    vframe, vnames = validator_frame(usable, base_cols) if not usable.empty else (pd.DataFrame(columns=["producer", "label"]), [])
    probe = {"method": method, "window_level": _cv(usable, PROBE_FEATURES), "validator_level": _cv(vframe, vnames)}
    baseline = {"method": method, "window_level": _cv(usable, base_cols), "validator_level": _cv(vframe, base_cols)}
    return probe, baseline

"""Simulation-recovery experiment: does the analysis find the rule that made the data?

Synthetic validators with known rules (simulate.py) are run through the same
tidy → model-selection path as real data, at realistic sizes (6–12 windows
per validator per kind, 8–12 probes per batch, 70–95% landing). The result is
a confusion matrix of generating rule vs selected rule.

Some rules make the same prediction as another rule on every probe of a given
design (for instance a batch auction and a plain priority order when no batch
mixes fee order and send order). For each validator the experiment measures
how often the generating rule's noise-free prediction differs from each
simpler rule on its own probes; when that separation is below 2% of pairs the
rule is *not identifiable under the design*, and accuracy is reported both
over all validators and over identifiable ones.

Two sweep designs are run: the shipped one (fee rising with send time, as in
`design.rs`) and the rejected alternative (fee falling with send time), which
makes a batch auction indistinguishable from priority ordering. The rejected
design is kept here because it is the reason the shipped one looks the way it
does.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from .constants import BATCH_W_GRID_MS, CORE_MODELS
from .models import fit_all, rule_order, sign_matrix
from .orders import OrderSet
from .simulate import Rule, SimValidator, generate, preset_validation
from .tidy import probes_frame

SEPARATION_MIN = 0.02
SELECTABLE = ("fifo", "priority_fifo_ties", "priority_lifo_ties", "batched", "random")
DESIGNS = {"shipped": "rising", "rejected_falling_sweep": "decreasing"}


def noise_free_signs(os: OrderSet, bb_ids: np.ndarray, rule: Rule, v: SimValidator) -> np.ndarray:
    """Pair signs the generating rule predicts from send times alone."""
    pairs = os.pairs_of(bb_ids)
    t = os.column("t_send_us", np.int64)
    p = os.column("agave_priority", np.int64)
    r = os.column("send_rank", np.int64)
    g = os.column("bb", np.int64)
    if rule.name == "batched":
        w = int(rule.w_ms * 1000)
        phase = int(round(rule.phase_ms * 1000 - v.delay_us)) % w
        order = rule_order("batched", t, p, r, group=g, w_us=w, phase_us=phase)
    else:
        order = rule_order(rule.expected_model, t, p, r, group=g)
    return os.signs_from_rank(os.ranks_from_order(order))[pairs]


def separation(os: OrderSet, bb_ids: np.ndarray, rule: Rule, v: SimValidator) -> dict[str, float]:
    """Share of the validator's pairs on which each simpler rule disagrees with the generating one."""
    pairs = os.pairs_of(bb_ids)
    if pairs.size == 0:
        return {}
    truth = noise_free_signs(os, bb_ids, rule, v)
    out = {}
    for name in CORE_MODELS:
        if name == rule.expected_model:
            continue
        pred = os.signs_from_rank(os.ranks_from_order(rule_order(name, os.column("t_send_us", np.int64), os.column("agave_priority", np.int64), os.column("send_rank", np.int64), group=os.column("bb", np.int64))))[pairs]
        out[name] = float(np.mean(pred != truth))
    return out


def run_design(seed: int, reps: int, sweep_fee: str, n_perm: int, n_boot: int) -> dict[str, Any]:
    cfg = preset_validation(seed, reps=reps, sweep_fee=sweep_fee)
    data = generate(cfg)
    ds = data.to_dataset()
    probes = probes_frame(ds.runs, ds.blocks)
    os = OrderSet.from_probes(probes)
    signs = sign_matrix(os)
    res = fit_all(os, signs, n_perm=n_perm, n_boot=n_boot)
    producer = os.bbs["producer"].astype(str).to_numpy()
    rows = []
    for v in cfg.validators:
        fit = res.validators.get(v.identity)
        ids = np.flatnonzero(producer == v.identity)
        if v.rule.name == "random":
            sep: dict[str, float] = {}
            identifiable = ids.size > 0
        else:
            sep = separation(os, ids, v.rule, v)
            identifiable = bool(sep) and min(sep.values()) >= SEPARATION_MIN
        mine = probes[probes["target"] == v.identity]
        kinds = mine.drop_duplicates("window_key")["kind"].value_counts()
        selected = fit.selected if fit is not None else "random"
        rows.append(
            {
                "validator": v.identity,
                "rule": v.rule.label,
                "generating": v.rule.name,
                "expected": v.rule.expected_model,
                "selected": selected,
                "best_model": fit.best if fit else None,
                "correct": selected == v.rule.expected_model,
                "identifiable": identifiable,
                "separation": sep,
                "p_perm": fit.p_perm if fit else None,
                "concordance": fit.concordance if fit else None,
                "theta": fit.theta if fit else None,
                "w_ms_true": v.rule.w_ms if v.rule.name == "batched" else None,
                "w_ms_selected": fit.w_ms if (fit and selected == "batched") else None,
                "threads_reorder": fit.by_arm.get("threads_reorder") if fit else None,
                "n_pairs": fit.n_pairs if fit else 0,
                "windows_per_kind": {k: int(kinds.get(k, 0)) for k in ("ladder", "tie", "sweep", "format")},
                "probes_per_batch": sorted({int(x) for x in mine.groupby("window_key").size()}),
                "landing_rate": float(mine.loc[mine["sent"], "landed"].mean()) if len(mine) else None,
            }
        )
    return summarise(rows, cfg, sweep_fee)


def summarise(rows: list[dict[str, Any]], cfg: Any, sweep_fee: str) -> dict[str, Any]:
    gens = [r for r in ("fifo", "priority_fifo_ties", "priority_lifo_ties", "batched", "threads", "random") if any(x["generating"] == r for x in rows)]
    confusion = {g: {s: sum(1 for x in rows if x["generating"] == g and x["selected"] == s) for s in SELECTABLE} for g in gens}
    ident = [x for x in rows if x["identifiable"]]
    per_rule = {}
    for g in gens:
        mine = [x for x in rows if x["generating"] == g]
        mine_id = [x for x in mine if x["identifiable"]]
        per_rule[g] = {
            "n": len(mine),
            "accuracy": float(np.mean([x["correct"] for x in mine])),
            "n_identifiable": len(mine_id),
            "accuracy_identifiable": float(np.mean([x["correct"] for x in mine_id])) if mine_id else None,
            "median_separation_from_closest_rule": float(np.median([min(x["separation"].values()) for x in mine if x["separation"]])) if any(x["separation"] for x in mine) and g != "random" else None,
        }
    batched = [x for x in rows if x["generating"] == "batched" and x["selected"] == "batched"]
    threads = [x for x in rows if x["generating"] == "threads"]
    non_threads_priority = [x for x in rows if x["generating"] in ("priority_fifo_ties", "priority_lifo_ties")]
    sizes = [x["windows_per_kind"] for x in rows]
    probes_per_batch = sorted({n for x in rows for n in x["probes_per_batch"]})
    return {
        "sweep_fee": sweep_fee,
        "n_validators": len(rows),
        "accuracy": float(np.mean([x["correct"] for x in rows])),
        "n_identifiable": len(ident),
        "accuracy_identifiable": float(np.mean([x["correct"] for x in ident])) if ident else None,
        "confusion": confusion,
        "per_rule": per_rule,
        "batched_w_abs_error_ms": [abs(x["w_ms_selected"] - x["w_ms_true"]) for x in batched if x["w_ms_selected"] is not None],
        "threads_arm_contrast_detected": float(np.mean([bool(x["threads_reorder"]) for x in threads])) if threads else None,
        "arm_contrast_false_positive": float(np.mean([bool(x["threads_reorder"]) for x in non_threads_priority])) if non_threads_priority else None,
        "sizes": {
            "windows_per_kind_min": int(min(min(s.values()) for s in sizes)),
            "windows_per_kind_max": int(max(max(s.values()) for s in sizes)),
            "windows_per_kind_mean": float(np.mean([np.mean(list(s.values())) for s in sizes])),
            "probes_per_batch": probes_per_batch,
            "landing_rate_mean": float(np.nanmean([x["landing_rate"] for x in rows if x["landing_rate"] is not None])),
            "pairs_per_validator_median": float(np.median([x["n_pairs"] for x in rows])),
        },
        "validators": rows,
    }


def run_validation(seed: int = 7, reps: int = 6, n_perm: int = 999, n_boot: int = 200, designs: dict[str, str] | None = None) -> dict[str, Any]:
    """The recovery experiment for every design."""
    designs = DESIGNS if designs is None else designs
    out = {
        "seed": seed,
        "validators_per_rule": reps,
        "separation_threshold": SEPARATION_MIN,
        "w_grid_ms": list(BATCH_W_GRID_MS),
        "expected_selection": {"threads": "priority_fifo_ties", "random": "random (no rule detectable)", "other": "the generating rule"},
        "designs": {name: run_design(seed, reps, fee, n_perm, n_boot) for name, fee in designs.items()},
    }
    out["headline"] = {
        name: {k: d[k] for k in ("accuracy", "accuracy_identifiable", "n_validators", "n_identifiable")}
        for name, d in out["designs"].items()
    }
    out["not_identifiable_under_design"] = {
        name: dict(Counter(x["generating"] for x in d["validators"] if not x["identifiable"])) for name, d in out["designs"].items()
    }
    return out

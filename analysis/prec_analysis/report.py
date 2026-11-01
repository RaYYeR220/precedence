"""Run the whole analysis and write deterministic JSON results.

Every output file is a pure function of the `data/` tree: fixed seeds, keys
sorted, floats rounded to 6 decimals, no timestamps or absolute paths. `check`
recomputes the outputs and compares them byte for byte.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import __version__
from .constants import ALPHA, BATCH_PASS_MAX_MS, BATCH_W_GRID_MS, FLOAT_DIGITS, N_BOOT, N_PERM, PHASE_STEPS, POWER_SIMS, orderer_class
from .card import CardContext, all_cards
from .controls import controls
from .entries import entries_report
from .fingerprint import fingerprint
from .inclusion import inclusion
from .load import Dataset, load_dataset
from .models import CANDIDATES, ModelResults, UnitFit, fit_all, sign_matrix
from .orders import OrderSet
from .power import power_all
from .stats import condition_rates, dose_response, format_effect, key_rates, tau_tables
from .tidy import tidy, windows_frame

OUTPUT_FILES = (
    "summary.json",
    "pairs_by_condition.json",
    "models.json",
    "inclusion.json",
    "fingerprint.json",
    "baseline.json",
    "controls.json",
    "power.json",
    "entries.json",
)


@dataclass(frozen=True)
class Settings:
    n_perm: int = N_PERM
    n_boot: int = N_BOOT
    power_sims: int = POWER_SIMS


# -- serialisation --------------------------------------------------------------


def to_jsonable(obj: Any) -> Any:
    """Plain JSON types; floats rounded, NaN/inf as null, numpy and pandas scalars unwrapped."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        x = float(obj)
        if not math.isfinite(x):
            return None
        x = round(x, FLOAT_DIGITS)
        return 0.0 if x == 0 else x
    if obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [to_jsonable(v) for v in obj.tolist()]
    return str(obj)


def dumps(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n"


# -- interpretation ---------------------------------------------------------------


def component_hint(fit: UnitFit) -> str:
    """Which component the evidence points at, in one line."""
    if fit.verdict != "rule":
        return "none detectable"
    if fit.best_name == "batched":
        # A continuous scheduler that re-sorts what has arrived every few ms looks
        # like a batch auction with a very short window; the window length is
        # what separates the two (the localnet Agave control fits w = 5 ms).
        if fit.w_ms is not None and fit.w_ms <= BATCH_PASS_MAX_MS:
            return (
                f"time-sliced priority: probes arriving within ~{fit.w_ms:g} ms are ordered by priority, "
                "later arrivals after them (a continuous scheduler's passes)"
            )
        return f"batch auction: batches of about {fit.w_ms:g} ms in time order, priority order inside each"
    if fit.best_name == "fifo":
        return "arrival order: no priority pass reached our probes"
    if fit.by_arm.get("threads_reorder"):
        return "scheduler priority pass, then reordered among non-conflicting probes by parallel execution (shared arm keeps priority order)"
    return "scheduler priority pass; no reordering by parallel execution detected"


def _attach_card(entry: dict[str, Any], card: dict[str, Any] | None) -> None:
    """Add the rule card; the headline text becomes its sentence, the single-model text is kept."""
    entry["single_model_text"] = entry.get("verdict_text")
    entry["single_model_component"] = entry.get("component")
    entry["rule_card"] = card
    if card is not None:
        entry["verdict_text"] = card["sentence"]
        entry["component"] = card_component(card) or entry.get("component")


def card_component(card: dict[str, Any]) -> str | None:
    """Which component decided the order, read from the rule card's parts."""
    parts: list[str] = []
    sim = card.get("simultaneous") or {}
    over = card.get("over_time") or {}
    entries = sim.get("entries") or {}
    if sim.get("detectable") and sim.get("rule") == "priority":
        same = (entries.get("same_entry") or {}).get("rate")
        cross = (entries.get("cross_entry") or {}).get("rate")
        if sim.get("scope") == "same_entry" and same is not None and cross is not None and cross < same - 0.1:
            parts.append("the scheduler sorts each batch it hands to a thread by priority; parallel threads then interleave those batches")
        else:
            parts.append("probes queued together are sorted by priority")
    if over.get("detectable"):
        batched = over.get("batched") or {}
        w = batched.get("w_ms")
        if over.get("rule") == "batched" and w is not None and w > BATCH_PASS_MAX_MS:
            parts.append(f"arrivals are grouped into ~{w:g} ms batches (a batch auction)")
        elif over.get("rule") == "fifo":
            parts.append("probes arriving apart are handled in arrival order (continuous scheduling)")
        elif over.get("rule") == "priority":
            parts.append("probes arriving tens of ms apart are still re-sorted by priority (they wait together)")
    return "; ".join(parts) if parts else None


def _unit_counts(probes: pd.DataFrame, col: str, value: str) -> dict[str, int]:
    own = probes[(probes["in_block"]) & (probes[col].astype(str) == value)]
    return {"n_windows": int(own["window_key"].nunique()), "n_landed_probes": int(len(own))}


# -- the analysis -------------------------------------------------------------------


def analyze(ds: Dataset, settings: Settings = Settings()) -> dict[str, Any]:
    """Every output file's content, keyed by file name."""
    probes, pairs, blocks_df = tidy(ds.runs, ds.blocks)
    windows = windows_frame(ds.runs)
    os = OrderSet.from_probes(probes) if not probes.empty else OrderSet.empty()
    signs = sign_matrix(os)
    res = fit_all(os, signs, n_perm=settings.n_perm, n_boot=settings.n_boot)
    power = power_all(os, signs, res, n_sim=settings.power_sims)
    fp, baseline = fingerprint(probes, os, signs, blocks_df) if not probes.empty else ({}, {})
    ctx = CardContext(os, signs, res.strata, res.strata_names, settings.n_perm, settings.n_boot, settings.power_sims)
    entry_os = OrderSet.from_probes(probes, by_entry=True) if not probes.empty and probes["entry_index"].notna().any() else OrderSet.empty()
    entry_ctx = CardContext.build(entry_os, settings.n_perm, settings.n_boot, settings.power_sims) if entry_os.n_bb else None
    cards = all_cards(ctx, res, entry_ctx)
    outputs: dict[str, Any] = {
        "pairs_by_condition": {
            "by_family": condition_rates(os, "producer_family", settings.n_boot, settings.n_perm),
            "by_validator": condition_rates(os, "producer", settings.n_boot, settings.n_perm),
            "dose_response_by_family": dose_response(os, "producer_family", settings.n_boot, settings.n_perm),
            "format_effect": format_effect(os, "producer_family", settings.n_boot, settings.n_perm),
            "batch_tau_by_family": tau_tables(os, "producer_family", settings.n_boot),
            "definitions": DEFINITIONS,
        },
        "models": models_json(res),
        "inclusion": inclusion(probes, windows),
        "fingerprint": fp,
        "baseline": baseline,
        "controls": controls(os, signs, blocks_df, settings.n_perm, settings.n_boot),
        "power": power,
        "entries": entries_report(os, probes, ds.runs, ds.blocks, blocks_df, settings.n_boot, settings.n_perm),
    }
    outputs["summary"] = summary_json(ds, probes, pairs, windows, os, res, power, fp, baseline, outputs, settings, cards)
    return {f"{name}.json": outputs[name] for name in (n[:-5] for n in OUTPUT_FILES)}


DEFINITIONS = {
    "higher_priority_first": "pairs with different agave_priority: share where the higher-priority probe precedes",
    "earlier_sent_first": "pairs with different send times: share where the earlier-sent probe precedes",
    "ci": "95% percentile interval, bootstrap over batches (windows)",
    "p_perm": "two-sided permutation p-value, positions permuted within each batch-block (null rate 0.5)",
    "dose_response": "higher_priority_first among pairs where the higher-priority probe was sent later by the bucket's gap",
    "tau_priority": "Kendall tau_b of priority vs position per batch-block, +1 = higher priority earlier",
    "tau_send": "Kendall tau_b of send time vs position per batch-block, +1 = earlier sent earlier",
    "units": "attribution by block producer; family = producer clientId in the snapshot nearest the window",
}


def models_json(res: ModelResults) -> dict[str, Any]:
    return {
        "method": {
            "likelihood": "Mallows on Kendall distance, theta >= 0 by maximum likelihood, one theta per unit",
            "selection": "AIC; batched counts theta, w and one phase per validator",
            "batched_check": f"a selected batched rule is kept only if it beats the best core rule beyond its grid search (parametric bootstrap, alpha {ALPHA})",
            "rule_vs_random": f"permutation test of the AIC gap to random, positions permuted within batch-blocks; rule declared at p < {ALPHA}",
            "w_grid_ms": list(BATCH_W_GRID_MS),
            "phase_steps": PHASE_STEPS,
            "candidates": CANDIDATES + ["random"],
        },
        "validators": {v: f.to_dict() for v, f in sorted(res.validators.items())},
        "families": {fam: f.to_dict() for fam, f in sorted(res.families.items())},
    }


def summary_json(
    ds: Dataset,
    probes: pd.DataFrame,
    pairs: pd.DataFrame,
    windows: pd.DataFrame,
    os: OrderSet,
    res: ModelResults,
    power: dict[str, Any],
    fp: dict[str, Any],
    baseline: dict[str, Any],
    outputs: dict[str, Any],
    settings: Settings,
    cards: tuple[dict[str, Any], dict[str, Any]] = ({}, {}),
) -> dict[str, Any]:
    landed = probes[probes["landed"]] if not probes.empty else probes
    inputs = {
        "analysis_version": __version__,
        "runs": [{"run_id": r.run_id, "design_sha256": r.design.get("sha256"), "mode": r.config.get("mode")} for r in ds.runs],
        "n_windows_planned": int(len(windows)),
        "n_windows_with_probes": int(probes["window_key"].nunique()) if not probes.empty else 0,
        "n_windows_sent": int(probes.loc[probes["sent"], "window_key"].nunique()) if not probes.empty else 0,
        "n_probes": int(len(probes)),
        "n_probes_sent": int(probes["sent"].sum()) if not probes.empty else 0,
        "n_probes_landed": int(len(landed)),
        "n_blocks": int(len(ds.blocks)),
        "n_batch_blocks": int(os.n_bb),
        "n_pairs": int(len(pairs)),
        "settings": {"n_perm": settings.n_perm, "n_boot": settings.n_boot, "power_sims": settings.power_sims, "alpha": ALPHA},
        "issues": list(ds.issues),
    }
    validators = {}
    for v, fit in sorted(res.validators.items()):
        fam = Counter(os.bbs.loc[os.bbs["producer"].astype(str) == v, "producer_family"].astype(str)).most_common()
        validators[v] = {
            "family": fam[0][0] if fam else None,
            "families_seen": sorted(f for f, _ in fam),
            "orderer_class": orderer_class(fam[0][0]) if fam else None,
            **_unit_counts(probes, "producer", v),
            **fit.summary(),
            "component": component_hint(fit),
            "threads_reorder": fit.by_arm.get("threads_reorder"),
            "key_rates": key_rates(os, "producer", v, settings.n_boot, settings.n_perm),
            "min_detectable": {k: power["validators"].get(v, {}).get(k) for k in ("theta_mde", "concordance_mde")},
        }
        _attach_card(validators[v], cards[0].get(v))
    families = {}
    for fam, fit in sorted(res.families.items()):
        members = sorted({b.validator for b in res.family_blocks.get(fam, [])})
        verdicts = Counter(res.validators[v].selected for v in members if v in res.validators)
        families[fam] = {
            "orderer_class": orderer_class(fam),
            "n_validators": len(members),
            **_unit_counts(probes, "producer_family", fam),
            **fit.summary(),
            "component": component_hint(fit),
            "threads_reorder": fit.by_arm.get("threads_reorder"),
            "validator_rules": dict(sorted(verdicts.items())),
            "key_rates": key_rates(os, "producer_family", fam, settings.n_boot, settings.n_perm),
            "min_detectable": {k: power["families"].get(fam, {}).get(k) for k in ("theta_mde", "concordance_mde")},
        }
        _attach_card(families[fam], cards[1].get(fam))
    ctrl = outputs["controls"]
    return {
        "inputs": inputs,
        "families": families,
        "validators": validators,
        "fingerprint": {
            level: {
                "probe_accuracy": fp.get(level, {}).get("accuracy"),
                "probe_balanced_accuracy": fp.get(level, {}).get("balanced_accuracy"),
                "baseline_accuracy": baseline.get(level, {}).get("accuracy"),
                "baseline_balanced_accuracy": baseline.get(level, {}).get("balanced_accuracy"),
                "chance_majority": fp.get(level, {}).get("chance_majority"),
                "chance_uniform": fp.get(level, {}).get("chance_uniform"),
                "n_rows": fp.get(level, {}).get("n_rows"),
                "n_validators": fp.get(level, {}).get("n_validators"),
            }
            for level in ("window_level", "validator_level")
        },
        "controls": {
            "shuffled_share_random": ctrl.get("shuffled_positions", {}).get("share_validators_random"),
            "shuffled_mean_tau_priority": ctrl.get("shuffled_positions", {}).get("tau_priority_ladder_format", {}).get("mean_tau"),
            "shuffled_sign_test_rejection_rate": ctrl.get("shuffled_positions", {}).get("tau_priority_ladder_format", {}).get("sign_test_rejection_rate"),
            "inert_rate": ctrl.get("inert_variable", {}).get("odd_idx_first", {}).get("rate"),
            "inert_p_perm": ctrl.get("inert_variable", {}).get("odd_idx_first", {}).get("p_perm"),
        },
    }


# -- files ---------------------------------------------------------------------------


def render(ds: Dataset, settings: Settings = Settings()) -> dict[str, str]:
    """File name → exact bytes (as text) of every output."""
    return {name: dumps(obj) for name, obj in analyze(ds, settings).items()}


def write_outputs(rendered: dict[str, str], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        with (out_dir / name).open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def check_outputs(rendered: dict[str, str], out_dir: Path) -> list[str]:
    """Names of output files that are missing or differ from `rendered`."""
    bad = []
    for name, text in rendered.items():
        path = out_dir / name
        if not path.exists() or path.read_bytes() != text.encode("utf-8"):
            bad.append(name)
    return bad


def run_analysis(data_dir: Path | str, out_dir: Path | str, check: bool = False, settings: Settings = Settings()) -> list[str]:
    """Analyse a data directory; write the results, or with `check` return the files that differ."""
    rendered = render(load_dataset(data_dir), settings)
    out = Path(out_dir)
    if check:
        return check_outputs(rendered, out)
    write_outputs(rendered, out)
    return []

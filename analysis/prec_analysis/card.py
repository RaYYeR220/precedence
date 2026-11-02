"""Rule cards: what decides the order of our probes, one design condition at a time.

A single best rule over all batches answers the wrong question when a leader
does different things in different situations: a scheduler that sorts what is
queued together by priority but takes later arrivals after earlier ones looks
like `fifo` once the time-spread sweeps dominate the pairs. The card therefore
runs the same Mallows/AIC selection and permutation test on each condition
separately, with only the rules that condition can tell apart:

* simultaneous   ladder + format bursts: priority vs send order vs random.
                 "Queued together" is observed directly where entries exist:
                 the probes of one entry were handed by one scheduler pass to
                 one execution thread, so the rule is selected on same-entry
                 groups and the cross-entry pairs show what happened between
                 passes and threads. Without entries all burst pairs are used.
* ties           tie batches: oldest first vs newest first vs random, by
                 spacing and arm;
* over_time      sweeps: send order vs priority vs batched(w), with the w
                 estimate and whether the w grid can express it on this data;
* conflicts      shared vs distinct arm under the card's own rules;
* format         v1 vs legacy at equal fee.

`sentence` states only the parts that beat random at alpha; every other part
is reported as "no rule detectable" with its minimum detectable effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .constants import ALPHA, BATCH_PASS_MAX_MS, BATCH_W_GRID_MS, N_BOOT, N_PERM, POWER_SIMS
from .models import (
    CANDIDATES,
    N_CORE,
    Block,
    ModelResults,
    UnitFit,
    arm_contrast,
    candidate_signs,
    fit_unit,
    make_block,
    sign_matrix,
    strata_of,
)
from .orders import OrderSet
from .power import power_curve
from .stats import fifo_pred, pair_rate, priority_pred

SEPARATION_MIN = 0.02
MIN_ENTRY_PAIRS = 10
BATCHED = tuple(range(N_CORE, len(CANDIDATES)))
COMPONENTS: dict[str, tuple[tuple[str, ...], dict[int, str]]] = {
    "simultaneous": (("ladder", "format"), {1: "priority", 0: "fifo"}),
    "ties": (("tie",), {0: "fifo", 2: "lifo"}),
    "over_time": (("sweep",), {0: "fifo", 1: "priority", **{i: "batched" for i in BATCHED}}),
}
Members = list[tuple[str, np.ndarray]]


@dataclass
class CardContext:
    """One set of observed orders with everything the cards need to fit on it."""

    os: OrderSet
    signs: np.ndarray
    strata: np.ndarray
    strata_names: list[str]
    n_perm: int = N_PERM
    n_boot: int = N_BOOT
    power_sims: int = POWER_SIMS
    blocks: dict[tuple[str, str, bytes], Block] = field(default_factory=dict)

    @classmethod
    def build(cls, os: OrderSet, n_perm: int = N_PERM, n_boot: int = N_BOOT, power_sims: int = POWER_SIMS) -> "CardContext":
        strata, names = strata_of(os)
        return cls(os, sign_matrix(os), strata, names, n_perm, n_boot, power_sims)

    @property
    def kinds(self) -> np.ndarray:
        return self.os.bbs["kind"].astype(str).to_numpy()

    @property
    def started_in_window(self) -> np.ndarray:
        """Batch-blocks whose first send was at or after their window's measured start
        (true when no exact slot time exists, e.g. a local validator that always leads).
        """
        if "send_phase_us" not in self.os.bbs:
            return np.ones(self.os.n_bb, dtype=bool)
        phase = pd.to_numeric(self.os.bbs["send_phase_us"], errors="coerce").to_numpy(dtype=float)
        return ~(phase < 0)

    def members(self, validators: list[str], family: str | None = None) -> Members:
        producer = self.os.bbs["producer"].astype(str).to_numpy()
        fam = self.os.bbs["producer_family"].astype(str).to_numpy()
        out = []
        for v in validators:
            keep = producer == v if family is None else (producer == v) & (fam == family)
            ids = np.flatnonzero(keep)
            if ids.size:
                out.append((v, ids))
        return out


@dataclass
class ComponentFit:
    name: str
    fit: UnitFit | None
    blocks: list[Block]
    bb_ids: np.ndarray
    ctx: CardContext
    scope: str = "all_pairs"

    @property
    def rules(self) -> dict[int, str]:
        return COMPONENTS[self.name][1]

    @property
    def has_data(self) -> bool:
        return self.fit is not None and self.fit.verdict != "insufficient_data"

    @property
    def detectable(self) -> bool:
        return self.has_data and self.fit.verdict == "rule"  # type: ignore[union-attr]

    @property
    def closest(self) -> str | None:
        return self.rules[self.fit.best_index] if self.has_data else None  # type: ignore[union-attr]

    @property
    def rule(self) -> str | None:
        """The component's rule if it beats random, `random` if not, None without data."""
        if self.closest is None:
            return None
        return self.closest if self.detectable else "random"


def fit_component(ctx: CardContext, name: str, label: str, members: Members) -> ComponentFit:
    """Select the component's rule for one unit; `members` are (validator, batch-block ids)."""
    comp_kinds, rules = COMPONENTS[name]
    kinds = ctx.kinds
    n_strata = len(ctx.strata_names)
    blocks = []
    in_window = ctx.started_in_window
    for validator, ids in members:
        sel = np.sort(ids[np.isin(kinds[ids], comp_kinds)])
        if name == "over_time":
            # A sweep says something about order over time only if it reached the
            # leader while it was building: one that started before the window sat
            # in the queue as a whole and was sorted like a burst.
            sel = sel[in_window[sel]]
        if sel.size == 0:
            continue
        key = (validator, name, sel.tobytes())
        if key not in ctx.blocks:
            ctx.blocks[key] = make_block(ctx.os, ctx.signs, ctx.strata, n_strata, validator, sel, ctx.n_perm, f"|card|{name}")
        if ctx.blocks[key].n_pairs:
            blocks.append(ctx.blocks[key])
    bb_ids = np.sort(np.concatenate([b.bb_ids for b in blocks])) if blocks else np.zeros(0, dtype=np.int64)
    fit = fit_unit(f"{label}|card|{name}", ctx.os, ctx.signs, ctx.strata, ctx.strata_names, blocks, ctx.n_boot, allowed=tuple(rules)) if blocks else None
    return ComponentFit(name, fit, blocks, bb_ids, ctx)


# -- component tables ---------------------------------------------------------------


def _fit_table(cf: ComponentFit, with_power: bool) -> dict[str, Any]:
    kinds = list(COMPONENTS[cf.name][0])
    if not cf.has_data:
        return {"kinds": kinds, "scope": cf.scope, "rule": None, "closest_rule": None, "detectable": False, "p_perm": None,
                "n_pairs": 0, "n_batch_blocks": 0, "mde": None, "note": "no batch of this kind with two probes in one unit"}
    fit = cf.fit
    assert fit is not None
    cands = []
    for row in fit.candidates:
        idx = CANDIDATES.index(row["id"]) if row["id"] in CANDIDATES else -1
        cands.append({"rule": "random" if idx < 0 else cf.rules[idx], "id": row["id"], "aic": row["aic"], "delta_aic": row["delta_aic"], "concordance": row["concordance"]})
    mde = None
    if with_power and not cf.detectable:
        ctx = cf.ctx
        pw = power_curve(ctx.os, ctx.signs, ctx.strata, len(ctx.strata_names), fit, cf.blocks, n_sim=ctx.power_sims)
        mde = {"theta": pw["theta_mde"], "concordance": pw["concordance_mde"]}
    return {
        "kinds": kinds,
        "scope": cf.scope,
        "rule": cf.rule,
        "closest_rule": cf.closest,
        "detectable": cf.detectable,
        "p_perm": fit.p_perm,
        "concordance": fit.concordance,
        "concordance_ci": fit.concordance_ci,
        "theta": fit.theta,
        "n_pairs": fit.n_pairs,
        "n_batch_blocks": fit.n_bb,
        "candidates": cands,
        "mde": mde,
    }


def _subset(ctx: CardContext, bb_ids: np.ndarray) -> OrderSet:
    mask = np.zeros(ctx.os.n_bb, dtype=bool)
    mask[bb_ids] = True
    return ctx.os.subset(mask)


def pick_scope(all_pairs: ComponentFit, same_entry: ComponentFit | None) -> ComponentFit:
    """Same-entry groups when they hold enough pairs, else every burst pair."""
    if same_entry is not None and same_entry.has_data and same_entry.fit.n_pairs >= MIN_ENTRY_PAIRS:  # type: ignore[union-attr]
        return same_entry
    return all_pairs


def card_fits(ctx: CardContext, label: str, members: Members, entry_ctx: CardContext | None = None, entry_members: Members | None = None) -> dict[str, ComponentFit]:
    """The three rule components of one unit; `simultaneous` already in its chosen scope."""
    sim = fit_component(ctx, "simultaneous", label, members)
    if entry_ctx is not None and entry_members:
        entry = fit_component(entry_ctx, "simultaneous", f"{label}|entry", entry_members)
        entry.scope = "same_entry"
        sim = pick_scope(sim, entry)
    return {
        "simultaneous": sim,
        "ties": fit_component(ctx, "ties", label, members),
        "over_time": fit_component(ctx, "over_time", label, members),
    }


def _simultaneous(all_pairs: ComponentFit, same_entry: ComponentFit | None, label: str, with_power: bool) -> tuple[dict[str, Any], ComponentFit]:
    chosen = pick_scope(all_pairs, same_entry)
    out = _fit_table(chosen, with_power)
    short = ("rule", "closest_rule", "detectable", "p_perm", "concordance", "concordance_ci", "n_pairs")
    full = _fit_table(all_pairs, with_power=False)
    out["all_pairs"] = {k: full.get(k) for k in short}
    ctx = all_pairs.ctx
    if not all_pairs.bb_ids.size:
        out["entries"] = {"available": False}
        return out, chosen
    sub = _subset(ctx, all_pairs.bb_ids)
    hp = priority_pred(sub)
    out["higher_priority_first"] = pair_rate(sub, hp, f"{label}|sim|hp", ctx.n_boot, ctx.n_perm)
    out["earlier_sent_first"] = pair_rate(sub, fifo_pred(sub), f"{label}|sim|es", ctx.n_boot, ctx.n_perm)
    entry = sub.column("entry_index")
    a, b = sub.pair_a, sub.pair_b
    known = np.isfinite(entry[a]) & np.isfinite(entry[b])
    if known.any():
        same = known & (entry[a] == entry[b])
        cross = known & (entry[a] != entry[b])
        out["entries"] = {
            "available": True,
            "same_entry": pair_rate(sub, np.where(same, hp, 0), f"{label}|sim|same", ctx.n_boot, ctx.n_perm),
            "cross_entry": pair_rate(sub, np.where(cross, hp, 0), f"{label}|sim|cross", ctx.n_boot, ctx.n_perm),
            "cross_entry_earlier_sent_first": pair_rate(sub, np.where(cross, fifo_pred(sub), 0), f"{label}|sim|cross-es", ctx.n_boot, ctx.n_perm),
        }
    else:
        out["entries"] = {"available": False}
    return out, chosen


def _ties(cf: ComponentFit, label: str, with_power: bool) -> dict[str, Any]:
    out = _fit_table(cf, with_power)
    if not cf.bb_ids.size:
        return out
    ctx = cf.ctx
    sub = _subset(ctx, cf.bb_ids)
    es = fifo_pred(sub)
    out["earlier_sent_first"] = pair_rate(sub, es, f"{label}|tie|es", ctx.n_boot, ctx.n_perm)
    spacing = sub.bbs["spacing_us"].to_numpy(dtype=float)
    arms = sub.bbs["arm"].astype(str).to_numpy()
    table: dict[str, Any] = {}
    for sp in sorted(set(spacing[np.isfinite(spacing)])):
        row = {}
        for arm in ("distinct", "shared", "all"):
            keep = (spacing == sp) & ((arms == arm) if arm != "all" else np.ones(arms.size, dtype=bool))
            if keep.any():
                row[arm] = pair_rate(sub, np.where(keep[sub.pair_bb], es, 0), f"{label}|tie|{sp}|{arm}", ctx.n_boot, ctx.n_perm)
        table[f"{sp / 1000:g}ms"] = row
    out["by_spacing"] = table
    return out


def _over_time(cf: ComponentFit, label: str, with_power: bool) -> dict[str, Any]:
    out = _fit_table(cf, with_power)
    if not cf.has_data:
        return out
    ctx, fit = cf.ctx, cf.fit
    assert fit is not None
    sub = _subset(ctx, cf.bb_ids)
    out["earlier_sent_first"] = pair_rate(sub, fifo_pred(sub), f"{label}|sweep|es", ctx.n_boot, ctx.n_perm)
    out["higher_priority_first"] = pair_rate(sub, priority_pred(sub), f"{label}|sweep|hp", ctx.n_boot, ctx.n_perm)
    batched = [c for c in fit.candidates if c["id"] in CANDIDATES and CANDIDATES.index(c["id"]) >= N_CORE]
    best = min(batched, key=lambda c: (c["aic"], CANDIDATES.index(c["id"])))
    best_idx = CANDIDATES.index(best["id"])
    w_best = float(BATCH_W_GRID_MS[best_idx - N_CORE])
    near = [float(BATCH_W_GRID_MS[CANDIDATES.index(c["id"]) - N_CORE]) for c in batched if c["aic"] - best["aic"] <= 2.0]
    pairs = ctx.os.pairs_of(cf.bb_ids)
    s_b = candidate_signs(ctx.signs, cf.blocks, best_idx)[pairs]
    sep_fifo = float(np.mean(s_b != ctx.signs[0, pairs]))
    sep_prio = float(np.mean(s_b != ctx.signs[1, pairs]))
    out["batched"] = {
        "w_ms": w_best if cf.rule == "batched" else None,
        "best_grid_w_ms": w_best,
        "w_range_ms": [min(near), max(near)],
        "separation_from_fifo": sep_fifo,
        "separation_from_priority": sep_prio,
        "w_identifiable": bool(min(sep_fifo, sep_prio) >= SEPARATION_MIN),
        "batched_check": fit.batched_check,
        "reads_as": None if cf.rule != "batched" else ("scheduler passes" if w_best <= BATCH_PASS_MAX_MS else "batch auction"),
    }
    return out


def _conflicts(ctx: CardContext, sim_rule: int | None, tie_rule: int | None, unit_ids: np.ndarray, label: str, single: UnitFit | None) -> dict[str, Any]:
    """Shared vs distinct under the card's own rules (bursts: simultaneous rule, ties: tie rule)."""
    s = np.zeros(ctx.os.n_pairs, dtype=np.int8)
    pair_kind = ctx.kinds[ctx.os.pair_bb]
    for rule, kinds in ((sim_rule, ("ladder", "format")), (tie_rule, ("tie",))):
        if rule is not None:
            sel = np.isin(pair_kind, kinds)
            s[sel] = ctx.signs[rule, sel]
    kinds = ctx.kinds[unit_ids]
    out: dict[str, Any] = {"all": arm_contrast(ctx.os, s, unit_ids, f"{label}|card|arm", ctx.n_boot)}
    for kind in ("ladder", "tie"):
        out[kind] = arm_contrast(ctx.os, s, unit_ids[kinds == kind], f"{label}|card|arm|{kind}", ctx.n_boot)
    out["threads_reorder"] = out["all"].get("threads_reorder")
    out["under_single_model"] = single.by_arm.get("shared_minus_distinct") if single is not None else None
    return out


def _format(ctx: CardContext, unit_ids: np.ndarray, label: str) -> dict[str, Any]:
    ids = unit_ids[ctx.kinds[unit_ids] == "format"]
    if ids.size == 0:
        return {"n_pairs": 0, "rate": None, "ci": [None, None], "p_perm": None, "detectable": False}
    sub = _subset(ctx, ids)
    fmt = sub.items["format"].astype(str).to_numpy()
    fee = sub.column("priority_fee")
    prio = sub.column("agave_priority")
    a, b = sub.pair_a, sub.pair_b
    same_fee = fee[a] == fee[b]
    v1_a = same_fee & (fmt[a] == "v1") & (fmt[b] == "legacy")
    v1_b = same_fee & (fmt[b] == "v1") & (fmt[a] == "legacy")
    out = pair_rate(sub, np.where(v1_a, 1, np.where(v1_b, -1, 0)).astype(np.int8), f"{label}|format", ctx.n_boot, ctx.n_perm)
    mixed = v1_a | v1_b
    v1_prio = np.where(v1_a, prio[a], prio[b])[mixed]
    legacy_prio = np.where(v1_a, prio[b], prio[a])[mixed]
    out["detectable"] = bool(out["p_perm"] is not None and out["p_perm"] < ALPHA)
    out["share_v1_higher_priority"] = float(np.mean(v1_prio > legacy_prio)) if mixed.any() else None
    return out


# -- the sentence ------------------------------------------------------------------


def _pct(rate: dict[str, Any] | None) -> str:
    if not rate or rate.get("rate") is None:
        return "n/a"
    return f"{100 * rate['rate']:.0f}%"


def _significant(rate: dict[str, Any] | None) -> bool:
    return bool(rate and rate.get("n_pairs") and rate.get("p_perm") is not None and rate["p_perm"] < ALPHA)


def _not_detectable(part: str, comp: dict[str, Any]) -> str:
    computed = comp.get("mde") is not None
    mde = (comp.get("mde") or {}).get("concordance")
    p = comp.get("p_perm")
    detail = f"p={p:.2f}" if p is not None else "too few pairs"
    if mde is not None:
        detail += f"; 80% power needs concordance >= {mde:.2f}"
    elif computed:
        detail += "; too few pairs for 80% power at any tested strength"
    return f"no rule detectable {part} ({detail})"


def sentence(card: dict[str, Any]) -> str:
    """One plain sentence from the card; only parts that beat random are claimed."""
    parts: list[str] = []
    sim, ties, over, conf, fmt = (card[k] for k in ("simultaneous", "ties", "over_time", "conflicts", "format"))
    entries = sim.get("entries") or {}
    if sim["rule"] == "priority":
        if sim["scope"] == "same_entry":
            detail = f"{_pct(entries.get('same_entry'))} within an entry"
            if _significant(entries.get("cross_entry")) or (entries.get("cross_entry") or {}).get("n_pairs"):
                detail += f", {_pct(entries.get('cross_entry'))} across entries"
        else:
            detail = f"{_pct(sim.get('higher_priority_first'))} of pairs"
        parts.append(f"priority among probes queued together ({detail})")
    elif sim["rule"] == "fifo":
        parts.append(f"send order among probes queued together ({_pct(sim.get('earlier_sent_first'))} of pairs)")
    elif sim["rule"] == "random":
        parts.append(_not_detectable("among probes queued together", sim))
    if over["rule"] == "fifo":
        parts.append(f"arrival order across scheduling passes (sweep {_pct(over.get('earlier_sent_first'))} send order)")
    elif over["rule"] == "priority":
        parts.append(f"priority even across time (sweep {_pct(over.get('higher_priority_first'))} fee order)")
    elif over["rule"] == "batched":
        w = over["batched"]["w_ms"]
        if w <= BATCH_PASS_MAX_MS:
            parts.append(f"priority within passes of about {w:g} ms, passes in arrival order")
        else:
            parts.append(f"batch auction of about {w:g} ms (batches in arrival order, priority inside)")
    elif over["rule"] == "random":
        parts.append(_not_detectable("over time", over))
    if ties["rule"] in ("fifo", "lifo"):
        r = (ties.get("earlier_sent_first") or {}).get("rate")
        word = "oldest" if ties["rule"] == "fifo" else "newest"
        if r is None:
            parts.append(f"ties {word} first")
        else:
            parts.append(f"ties {word} first ({100 * (r if ties['rule'] == 'fifo' else 1 - r):.0f}%)")
    elif ties["rule"] == "random":
        parts.append(_not_detectable("for ties", ties))
    tail = []
    if conf.get("threads_reorder"):
        d = conf["all"]["distinct"]["concordance"]
        s = conf["all"]["shared"]["concordance"]
        tail.append(f"non-conflicting probes are interleaved by parallel execution ({100 * d:.0f}% in rule order vs {100 * s:.0f}% when they conflict)")
    if fmt.get("detectable"):
        ahead = "v1" if fmt["rate"] > 0.5 else "legacy"
        tail.append(f"{ahead} ahead at equal fee ({100 * max(fmt['rate'], 1 - fmt['rate']):.0f}%)")
    if not parts and not tail:
        return "No rule detectable (no batch with two probes in one block)."
    text = "; ".join([", ".join(parts)] + tail) if parts else "; ".join(tail)
    return text[0].upper() + text[1:] + "."


# -- cards ------------------------------------------------------------------------------


def build_card(
    ctx: CardContext,
    label: str,
    members: Members,
    single: UnitFit | None,
    entry_ctx: CardContext | None = None,
    entry_members: Members | None = None,
    with_power: bool = True,
) -> dict[str, Any]:
    """The rule card of one unit (validator or family)."""
    sim_all = fit_component(ctx, "simultaneous", label, members)
    sim_entry = None
    if entry_ctx is not None and entry_members:
        sim_entry = fit_component(entry_ctx, "simultaneous", f"{label}|entry", entry_members)
        sim_entry.scope = "same_entry"
    ties = fit_component(ctx, "ties", label, members)
    over = fit_component(ctx, "over_time", label, members)
    unit_ids = np.sort(np.concatenate([ids for _, ids in members])) if members else np.zeros(0, dtype=np.int64)
    sim_table, sim_chosen = _simultaneous(sim_all, sim_entry, label, with_power)
    card = {
        "simultaneous": sim_table,
        "ties": _ties(ties, label, with_power),
        "over_time": _over_time(over, label, with_power),
        "conflicts": _conflicts(ctx, _rule_index(sim_chosen), _rule_index(ties), unit_ids, label, single),
        "format": _format(ctx, unit_ids, label),
        "alpha": ALPHA,
    }
    card["sentence"] = sentence(card)
    return card


def _rule_index(cf: ComponentFit) -> int | None:
    return cf.fit.best_index if cf.has_data else None  # type: ignore[union-attr]


def all_cards(ctx: CardContext, results: ModelResults, entry_ctx: CardContext | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rule cards for every validator and every family."""
    if ctx.os.n_bb == 0:
        return {}, {}
    validators = {}
    for v in sorted(results.validators):
        em = entry_ctx.members([v]) if entry_ctx is not None and entry_ctx.os.n_bb else None
        validators[v] = build_card(ctx, f"validator:{v}", ctx.members([v]), results.validators[v], entry_ctx, em)
    families = {}
    for fam in sorted(results.families):
        vs = sorted({b.validator for b in results.family_blocks.get(fam, [])})
        em = entry_ctx.members(vs, fam) if entry_ctx is not None and entry_ctx.os.n_bb else None
        families[fam] = build_card(ctx, f"family:{fam}", ctx.members(vs, fam), results.families[fam], entry_ctx, em)
    return validators, families

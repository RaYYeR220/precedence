"""The generated Markdown blocks of README.md and REPORT.md.

Each function turns the results (and, for run bookkeeping, the data tree)
into one Markdown fragment. No number in a fragment is typed by hand. A
block with nothing to show says so in one italic line, so a README rendered
before the first run is still valid.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable

from ..card import SEPARATION_MIN
from ..constants import FAMILIES, GAP_BUCKETS_MS
from .context import Context
from .fmt import (
    DASH,
    ORDERER_CLASS_TEXT,
    CARD_PARTS,
    CARD_PART_TITLES,
    account_link,
    card_cell,
    card_part,
    dec,
    dec_ci,
    gap_label,
    ms,
    is_num,
    lamports,
    model_text,
    note,
    num,
    pct,
    pct_ci,
    plural,
    pval,
    rate_ci,
    rule_text,
    strictness,
    table,
)
from .predictions import evaluate, outcome_counts

NO_RESULTS = note("No results yet: run `python -m prec_analysis analyze` first.")
NO_DATA = note("No run in the data directory.")

DOSE_LABELS = {"hp_sent_earlier_or_same": "sent earlier or at the same time"} | {name: f"{gap_label(name)} later" for _, _, name in GAP_BUCKETS_MS}


def family_order(names: list[str]) -> list[str]:
    """Study families in their fixed order, then anything else alphabetically."""
    known = [f for f in FAMILIES if f in names]
    return known + sorted(n for n in names if n not in FAMILIES)


def _rule_cell(unit: dict[str, Any]) -> str:
    if unit.get("verdict") == "insufficient_data":
        return "insufficient data"
    if unit.get("verdict") != "rule":
        ref = unit.get("reference_model")
        return f"none detectable; closest {model_text(ref)}" if ref else "none detectable"
    return rule_text(unit.get("best_rule"), unit.get("w_ms"))


def _single_model_cell(unit: dict[str, Any]) -> str:
    """The best single rule over all batches, with its permutation p and near ties."""
    text = _rule_cell(unit)
    if unit.get("verdict") in ("rule", "no_rule_detectable"):
        text += f" (p = {pval(unit.get('p_perm'))})"
    indist = unit.get("indistinguishable_from") or []
    if indist:
        text += "; ΔAIC ≤ 2 to " + ", ".join(model_text(m) for m in indist)
    return text


def verdict_sentence(unit: dict[str, Any]) -> str:
    """`rule_card.sentence` (what decides the order, condition by condition), else `verdict_text`."""
    card = unit.get("rule_card") or {}
    return str(card.get("sentence") or unit.get("verdict_text") or DASH)


def card_cells(unit: dict[str, Any]) -> list[str]:
    """Compact cells for the rule card's three components: queued together, ties, over time."""
    card = unit.get("rule_card") or {}
    return [card_cell(name, card.get(name)) for name in CARD_PARTS]


def _validator_mix(fam: dict[str, Any]) -> str:
    rules = fam.get("validator_rules") or {}
    if not rules:
        return ""
    return ", ".join(f"{n} {rule_text(k)}" for k, n in sorted(rules.items(), key=lambda kv: (-kv[1], kv[0])))


def _stake(ctx: Context, family: str | None = None, validator: str | None = None) -> str:
    if ctx.data is None:
        return DASH
    share = ctx.data.stake_share_of_family(family) if family is not None else ctx.data.stake_share_of(validator or "")
    return pct(share) if share is not None else DASH


# -- headline ---------------------------------------------------------------------


def headline_items(ctx: Context) -> list[str]:
    """3–5 one-line key numbers, shared by the README and the explorer."""
    s = ctx.summary
    if not s:
        return []
    inp = s.get("inputs", {})
    inc = ctx.r("inclusion").get("overall", {})
    items = [
        f"**{num(inp.get('n_probes_landed'))}** of {num(inp.get('n_probes_sent'))} probes landed ({pct(inc.get('rate'))}), "
        f"in {plural(inp.get('n_windows_sent'), 'leader window')} over {plural(len(inp.get('runs', [])), 'run')} and {plural(inp.get('n_blocks'), 'block')}."
    ]
    fams = ctx.families()
    vals = ctx.validators()
    fam_fit = [f for f in fams.values() if f.get("verdict") != "insufficient_data"]
    val_fit = [v for v in vals.values() if v.get("verdict") != "insufficient_data"]
    if fam_fit:
        items.append(
            f"A rule was detected for **{sum(f.get('verdict') == 'rule' for f in fam_fit)} of {len(fam_fit)}** client families "
            f"and **{sum(v.get('verdict') == 'rule' for v in val_fit)} of {len(val_fit)}** validators with at least one pair of probes in one block."
        )
    card_line = _card_headline(fams)
    if card_line:
        items.append(card_line)
    fp = s.get("fingerprint", {}).get("validator_level", {})
    if is_num(fp.get("probe_accuracy")):
        items.append(
            f"Our probes alone name the orderer of **{pct(fp['probe_accuracy'])}** of validators "
            f"(whole-block baseline {pct(fp.get('baseline_accuracy'))}, chance {pct(fp.get('chance_majority'))})."
        )
    outcomes = evaluate(ctx)
    c = outcome_counts(outcomes)
    items.append(f"Pre-registered predictions: **{c['confirmed']} confirmed**, {c['refuted']} refuted, {c['inconclusive']} inconclusive of {len(outcomes)}.")
    return items[:5]


def _card_headline(fams: dict[str, dict[str, Any]]) -> str | None:
    """How many families each rule decides, for probes queued together and over time."""

    def tally(part: str) -> str:
        counts: Counter[str] = Counter()
        for f in fams.values():
            comp = (f.get("rule_card") or {}).get(part) or {}
            if comp.get("rule") is not None:
                counts[card_part(part, comp)[0].split(",")[0]] += 1
        return ", ".join(f"{label} in {n}" for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    sim, over = tally("simultaneous"), tally("over_time")
    if not sim and not over:
        return None
    n = sum(1 for f in fams.values() if f.get("rule_card"))
    return f"Of {plural(n, 'family', 'families')}, probes queued together are ordered by **{sim or 'nothing detectable'}**; probes spread over time by {over or 'nothing detectable'}."


def block_headline(ctx: Context) -> str:
    items = headline_items(ctx)
    if not items:
        return NO_RESULTS
    return "\n".join(f"- {line}" for line in items)


# -- verdicts ----------------------------------------------------------------------


def block_verdicts(ctx: Context) -> str:
    fams = ctx.families()
    if not fams:
        return NO_RESULTS
    rows = []
    for name in family_order(list(fams)):
        f = fams[name]
        mix = _validator_mix(f)
        rows.append(
            [
                f"{name} ({ORDERER_CLASS_TEXT.get(f.get('orderer_class') or '', DASH)})",
                _stake(ctx, family=name),
                num(f.get("n_validators")),
                num(f.get("n_windows")),
                num(f.get("n_landed_probes")),
                *card_cells(f),
                _single_model_cell(f) + (f"; validators: {mix}" if mix else ""),
                strictness(f),
                f.get("component") or DASH,
                verdict_sentence(f),
            ]
        )
    return table(
        ["Family", "Stake", "Validators", "Windows", "Landed probes", *(CARD_PART_TITLES[n] for n in CARD_PARTS), "Best single rule", "Strictness: concordance [CI], θ", "Component", "Verdict"],
        rows,
        "lrrrrlllllll",
    )


def block_per_validator(ctx: Context) -> str:
    vals = ctx.validators()
    if not vals:
        return NO_RESULTS
    order = family_order(sorted({str(v.get("family")) for v in vals.values()}))
    rank = {f: i for i, f in enumerate(order)}
    keyed = sorted(vals.items(), key=lambda kv: (rank.get(str(kv[1].get("family")), 99), -(kv[1].get("n_landed_probes") or 0), kv[0]))
    rows = []
    for v, s in keyed:
        fams = s.get("families_seen") or []
        fam = str(s.get("family") or DASH) + (f" (also {', '.join(x for x in fams if x != s.get('family'))})" if len(fams) > 1 else "")
        mde = s.get("min_detectable", {}) or {}
        rows.append(
            [
                account_link(v),
                fam,
                _stake(ctx, validator=v),
                num(s.get("n_windows")),
                num(s.get("n_landed_probes")),
                *card_cells(s),
                _single_model_cell(s),
                strictness(s),
                s.get("component") or DASH,
                verdict_sentence(s),
                pct(mde.get("concordance_mde")),
            ]
        )
    return table(
        ["Validator", "Family", "Stake", "Windows", "Landed probes", *(CARD_PART_TITLES[n] for n in CARD_PARTS), "Best single rule", "Strictness: concordance [CI], θ", "Component", "Verdict", "Smallest detectable concordance"],
        rows,
        "llrrrllllllll",
    )


# -- predictions -------------------------------------------------------------------


def block_predictions(ctx: Context) -> str:
    outcomes = evaluate(ctx)
    return table(["Id", "Prediction", "Outcome", "Evidence"], [[o.id, o.prediction, o.outcome, o.evidence] for o in outcomes])


# -- dose-response -----------------------------------------------------------------


def block_dose_response(ctx: Context) -> str:
    dose = ctx.r("pairs_by_condition").get("dose_response_by_family", {})
    if not dose:
        return NO_RESULTS
    buckets = ["hp_sent_earlier_or_same"] + [name for _, _, name in GAP_BUCKETS_MS]
    units = ["all"] + family_order([u for u in dose if u != "all"])
    rows = []
    for unit in units:
        by_bucket = {r.get("bucket"): r for r in dose.get(unit, [])}
        rows.append(["all families" if unit == "all" else unit] + [rate_ci(by_bucket.get(b), with_n=True) if (by_bucket.get(b) or {}).get("n_pairs") else DASH for b in buckets])
    head = ["Higher-priority probe was…"] + [DOSE_LABELS[b] for b in buckets]
    return (
        table(head, rows, "l" + "r" * len(buckets))
        + "\n\n"
        + note("Share of pairs where the higher-priority probe came first, by how much later it was sent; 95% interval over batches, n = pairs.")
    )


# -- ties --------------------------------------------------------------------------


def _direction(rate: dict[str, Any] | None) -> str:
    if not rate or not is_num(rate.get("rate")) or not all(is_num(v) for v in (rate.get("ci") or [None, None])):
        return DASH
    lo, hi = rate["ci"]
    if lo > 0.5:
        return "oldest first (FIFO)"
    if hi < 0.5:
        return "newest first (LIFO)"
    return "no reliable order"


def block_ties(ctx: Context) -> str:
    fams = ctx.families()
    by_family = ctx.r("pairs_by_condition").get("by_family", {})
    if not fams:
        return NO_RESULTS
    rows = []
    for name in family_order(list(fams)):
        tie = (fams[name].get("key_rates") or {}).get("earlier_sent_first_tie")
        if not tie or not tie.get("n_pairs"):
            continue
        cells = by_family.get(name, {})
        spacing = ((fams[name].get("rule_card") or {}).get("ties") or {}).get("by_spacing") or {}
        rows.append(
            [
                name,
                num(tie.get("n_pairs")),
                num(tie.get("n_batches")),
                rate_ci(tie),
                pval(tie.get("p_perm")),
                _direction(tie),
                *[_spacing_cell((spacing.get(sp) or {}).get("all")) for sp in TIE_SPACINGS],
                rate_ci((cells.get("tie/distinct") or {}).get("earlier_sent_first")),
                rate_ci((cells.get("tie/shared") or {}).get("earlier_sent_first")),
            ]
        )
    if not rows:
        return note("No tie batch had two probes in one block.")
    return (
        table(["Family", "Tie pairs", "Batches", "Earlier sent first", "p", "Direction", *(f"{sp.replace('ms', ' ms')} apart" for sp in TIE_SPACINGS), "Distinct arm", "Shared arm"], rows, "lrrrrlrrrrr")
        + "\n\n"
        + note("Earlier sent first: share of equal-fee pairs where the earlier-sent probe came first; by spacing from the rule card, b = batches.")
    )


TIE_SPACINGS = ("0.5ms", "2ms", "5ms")


def _spacing_cell(rate: dict[str, Any] | None) -> str:
    if not rate or not rate.get("n_pairs"):
        return DASH
    return f"{rate_ci(rate)} b={num(rate.get('n_batches'))}"


# -- sweep -------------------------------------------------------------------------


def block_sweep(ctx: Context) -> str:
    models = ctx.r("models").get("validators", {})
    vals = ctx.validators()
    by_val = ctx.r("pairs_by_condition").get("by_validator", {})
    if not models:
        return NO_RESULTS
    order = family_order(sorted({str(v.get("family")) for v in vals.values()}))
    rank = {f: i for i, f in enumerate(order)}
    rows = []
    for v in sorted(models, key=lambda k: (rank.get(str(vals.get(k, {}).get("family")), 99), k)):
        m = models[v]
        sweep = by_val.get(v, {}).get("sweep/all")
        if not sweep:
            continue
        over = (vals.get(v, {}).get("rule_card") or {}).get("over_time") or {}
        b = over.get("batched") or {}
        check = b.get("batched_check") or {}
        rng = b.get("w_range_ms") or []
        rows.append(
            [
                account_link(v),
                vals.get(v, {}).get("family") or DASH,
                card_cell("over_time", over) if over else DASH,
                f"{ms(b.get('best_grid_w_ms'))}" + (f" ({ms(rng[0])}–{ms(rng[1])})" if len(rng) == 2 and rng[0] != rng[1] else "") if b else DASH,
                ("yes" if b.get("w_identifiable") else "no") if b else DASH,
                rate_ci(sweep.get("higher_priority_first"), with_n=True),
                rate_ci(sweep.get("earlier_sent_first")),
                (f"{pval(check.get('p'))} ({'kept' if check.get('kept') else 'fell back to ' + model_text(check.get('core_rule'))})" if check else DASH),
                _rule_cell(m),
            ]
        )
    if not rows:
        return note("No sweep batch had two probes in one block.")
    return (
        table(["Validator", "Family", "Over-time rule (rule card)", "Best batch length w (ΔAIC ≤ 2 range)", "w identifiable", "Sweep: higher priority first", "Sweep: earlier sent first", "Batched check p", "Best single rule, all batches"], rows, "lllrlrrll")
        + "\n\n"
        + note(f"Sweeps raise the fee with send time, so fee order and send order disagree on every pair; a batch length is identifiable when it orders at least {pct(SEPARATION_MIN, 0)} of pairs differently from both.")
    )


# -- inclusion ---------------------------------------------------------------------


def block_inclusion(ctx: Context) -> str:
    inc = ctx.r("inclusion")
    if not inc.get("by_family"):
        return NO_RESULTS
    spill = {r["target_family"]: r for r in inc.get("spill_by_family", [])}
    path = {(r["target_family"], r["path"]): r for r in inc.get("by_family_path", [])}
    in_window: dict[str, list[float]] = {}
    for r in inc.get("landing_slot", []):
        if is_num(r.get("share_in_window")) and r.get("n"):
            acc = in_window.setdefault(r["target_family"], [0.0, 0.0])
            acc[0] += r["share_in_window"] * r["n"]
            acc[1] += r["n"]

    def rate(r: dict[str, Any] | None) -> str:
        if not r or not r.get("n"):
            return DASH
        return f"{pct(r.get('rate'))} {pct_ci(r.get('ci'))}"

    rows = []
    fams = {r["target_family"]: r for r in inc["by_family"]}
    for name in family_order(list(fams)):
        r = fams[name]
        w = in_window.get(name)
        rows.append(
            [
                name,
                num(r.get("n")),
                rate(r),
                rate(path.get((name, "quic"))),
                rate(path.get((name, "rpc"))),
                rate(spill.get(name)),
                pct(w[0] / w[1]) if w and w[1] else DASH,
            ]
        )
    overall = inc.get("overall", {})
    rows.append(["all", num(overall.get("n")), rate(overall), DASH, DASH, DASH, DASH])
    return (
        table(["Target family", "Probes sent", "Landed", "Landed via QUIC", "Landed via RPC", "Landed in another leader's block", "Landed inside the 4-slot window"], rows, "lrrrrrr")
        + "\n\n"
        + note("Grouped by the family of the leader we targeted; 95% Wilson intervals.")
    )


# -- fingerprint -------------------------------------------------------------------


def block_fingerprint(ctx: Context) -> str:
    fp, base = ctx.r("fingerprint"), ctx.r("baseline")
    if not fp:
        return NO_RESULTS
    rows = []
    for level, label in (("validator_level", "per validator"), ("window_level", "per window")):
        a, b = fp.get(level, {}), base.get(level, {})
        if not is_num(a.get("accuracy")):
            rows.append([label, a.get("note") or "not enough data", DASH, DASH, DASH, DASH, num(a.get("n_rows"))])
            continue
        rows.append(
            [
                label,
                pct(a.get("accuracy")),
                pct(a.get("balanced_accuracy")),
                pct(b.get("accuracy")),
                pct(b.get("balanced_accuracy")),
                f"{pct(a.get('chance_majority'))} / {pct(a.get('chance_uniform'))}",
                num(a.get("n_rows")),
            ]
        )
    out = table(["Classifier", "Our probes", "Balanced", "Whole-block baseline", "Balanced", "Chance (majority / uniform)", "Rows"], rows, "lrrrrrr")
    conf = fp.get("validator_level", {}).get("confusion")
    if conf:
        classes = list(conf)
        crow = [[ORDERER_CLASS_TEXT.get(t, t)] + [num(conf[t].get(p)) for p in classes] for t in classes]
        out += "\n\n" + table(["True orderer \\ predicted"] + [ORDERER_CLASS_TEXT.get(p, p) for p in classes], crow, "l" + "r" * len(classes))
        out += "\n\n" + note("Leave-one-validator-out: no validator is scored by a model that saw it. Confusion counts validators.")
    return out


# -- controls ----------------------------------------------------------------------


def block_controls(ctx: Context) -> str:
    c = ctx.r("controls")
    if not c:
        return NO_RESULTS
    sh = c.get("shuffled_positions", {})
    tp = sh.get("tau_priority_ladder_format", {})
    ts = sh.get("tau_send_tie_sweep", {})
    inert = c.get("inert_variable", {})
    odd = inert.get("odd_idx_first", {})
    tpar = inert.get("tau_parity", {})
    rows = [
        ["Shuffled positions", "validators selecting no rule", "≈ 95%", pct(sh.get("share_validators_random"))],
        ["Shuffled positions", "families selecting no rule", "≈ 95%", pct(sh.get("share_families_random"))],
        ["Shuffled positions", "mean τ, priority vs position (ladder, format)", "≈ 0", f"{dec(tp.get('mean_tau'), 3)} {dec_ci(tp.get('range_95'), 3)}"],
        ["Shuffled positions", "sign test fires (ladder, format)", "≈ 5%", pct(tp.get("sign_test_rejection_rate"))],
        ["Shuffled positions", "mean τ, send time vs position (tie, sweep)", "≈ 0", f"{dec(ts.get('mean_tau'), 3)} {dec_ci(ts.get('range_95'), 3)}"],
        ["Inert variable (idx parity)", "odd idx first", "≈ 50%", f"{rate_ci(odd, with_n=True)}, p = {pval(odd.get('p_perm'))}"],
        ["Inert variable (idx parity)", "mean τ", "≈ 0", f"{dec(tpar.get('mean'), 3)} {dec_ci(tpar.get('ci'), 3)}, sign test p = {pval(tpar.get('sign_test_p'))}"],
    ]
    out = table(["Control", "Statistic", "Expected", "Observed"], rows, "lllr")
    obs = c.get("whole_block_observational", {})
    if obs:
        orow = []
        for fam in family_order(list(obs)):
            o = obs[fam]
            orow.append(
                [
                    fam,
                    num(o.get("n_blocks")),
                    f"{dec((o.get('spearman_priority_index') or {}).get('mean'), 3)} {dec_ci((o.get('spearman_priority_index') or {}).get('ci'), 3)}",
                    pct((o.get("adjacent_monotonicity") or {}).get("mean")),
                    pct((o.get("share_zero_priority") or {}).get("mean")),
                    dec((o.get("n_tx") or {}).get("mean"), 0),
                ]
            )
        out += "\n\n" + table(["Family", "Blocks", "Spearman priority vs index", "Adjacent pairs falling in priority", "Zero-priority share", "Mean tx per block"], orow, "lrrrrr")
        out += "\n\n" + note("What an observer of whole blocks sees without our probes (supplementary; negative Spearman = higher priority earlier).")
    return out


# -- power -------------------------------------------------------------------------


def block_power(ctx: Context) -> str:
    p = ctx.r("power")
    if not p.get("families") and not p.get("validators"):
        return NO_RESULTS

    def row(name: str, r: dict[str, Any], link: bool) -> list[str]:
        return [
            account_link(name) if link else name,
            model_text(r.get("reference_model")),
            num(r.get("n_pairs")),
            num(r.get("n_batch_blocks")),
            dec(r.get("theta_mde")),
            pct(r.get("concordance_mde")),
            r.get("note") or "",
        ]

    fams = p.get("families", {})
    out = table(["Family", "Reference rule", "Pairs", "Batch-blocks", "Smallest detectable θ", "≈ concordance", "Note"], [row(f, fams[f], False) for f in family_order(list(fams))], "llrrrrl")
    vals = p.get("validators", {})
    if vals:
        fam_of = {v: s.get("family") for v, s in ctx.validators().items()}
        order = family_order(sorted({str(x) for x in fam_of.values()}))
        rank = {f: i for i, f in enumerate(order)}
        keyed = sorted(vals, key=lambda v: (rank.get(str(fam_of.get(v)), 99), v))
        out += "\n\n" + table(["Validator", "Reference rule", "Pairs", "Batch-blocks", "Smallest detectable θ", "≈ concordance", "Note"], [row(v, vals[v], True) for v in keyed], "llrrrrl")
    out += "\n\n" + note("Smallest Mallows θ the rule-vs-random test detects with 80% power at α = 0.05, from simulations at the unit's own sizes.")
    return out


# -- entries -----------------------------------------------------------------------


def block_entries(ctx: Context) -> str:
    e = ctx.r("entries")
    if not e:
        return NO_RESULTS
    if not e.get("available"):
        return note(f"Entry data not available: {e.get('reason', 'no entries recorded')}.")
    fams = e.get("families", {})
    rows = []
    for name in family_order(list(fams)):
        f = fams[name]
        hp, es = f.get("higher_priority_first", {}), f.get("earlier_sent_first_tie", {})
        bsz = f.get("block_entry_sizes", {})
        rows.append(
            [
                name,
                num(f.get("n_pairs_same_entry")),
                num(f.get("n_pairs_cross_entry")),
                rate_ci(hp.get("same_entry")),
                rate_ci(hp.get("cross_entry")),
                rate_ci(es.get("same_entry")),
                rate_ci(es.get("cross_entry")),
                dec(bsz.get("mean"), 1),
                pct(bsz.get("share_single_tx")),
            ]
        )
    return (
        table(
            ["Family", "Pairs in one entry", "Pairs across entries", "Priority first, same entry", "Priority first, across", "Tie FIFO, same entry", "Tie FIFO, across", "Mean entry size", "Single-tx entries"],
            rows,
            "lrrrrrrrr",
        )
        + "\n\n"
        + note("Inside one entry the order is the scheduler's hand-off to one thread; across entries it is the order threads finished.")
    )


# -- measurements and what went wrong ----------------------------------------------


def _utc(us: Any) -> str:
    if not is_num(us):
        return DASH
    return datetime.fromtimestamp(int(us) / 1e6, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _skips(counts: dict[str, int]) -> str:
    skipped = {k.split(":", 1)[1]: v for k, v in counts.items() if k.startswith("skipped:")}
    return ", ".join(f"{v} {k}" for k, v in sorted(skipped.items())) or "0"


def block_measurements(ctx: Context) -> str:
    if ctx.data is None:
        return NO_DATA
    rows = []
    tot = Counter()
    for r in ctx.data.runs:
        sent_batches = r.status_counts.get("sent", 0) + r.status_counts.get("dry", 0)
        slots = f"{num(r.first_slot)}–{num(r.last_slot)}" if r.first_slot is not None else DASH
        rows.append(
            [
                f"`{r.run_id}`",
                r.mode or DASH,
                _utc(r.started_unix_us),
                _utc(r.ended_unix_us),
                slots,
                f"{num(sent_batches)} of {num(r.n_batches)}",
                _skips(r.status_counts),
                num(r.n_sent),
                num(r.n_landed),
                lamports(r.reserved) if r.n_reserved else DASH,
                lamports(r.settled) if r.n_settled else DASH,
            ]
        )
        tot.update({"batches": r.n_batches, "sent_batches": sent_batches, "sent": r.n_sent, "landed": r.n_landed, "reserved": r.reserved, "settled": r.settled})
    if len(rows) > 1:
        all_status: Counter[str] = Counter()
        for r in ctx.data.runs:
            all_status.update(r.status_counts)
        rows.append(["all runs", DASH, DASH, DASH, DASH, f"{num(tot['sent_batches'])} of {num(tot['batches'])}", _skips(dict(all_status)), num(tot["sent"]), num(tot["landed"]), lamports(tot["reserved"]), lamports(tot["settled"])])
    return table(
        ["Run", "Mode", "Start (UTC)", "End (UTC)", "Slots covered", "Batches sent", "Skipped by reason", "Probes sent", "Landed", "Reserved (lamports)", "Settled (lamports)"],
        rows,
        "lllllrlrrrr",
    )


def block_what_went_wrong_stats(ctx: Context) -> str:
    if ctx.data is None and not ctx.results:
        return NO_DATA
    rows: list[list[str]] = []
    if ctx.data is not None:
        skips: Counter[str] = Counter()
        errors: Counter[str] = Counter()
        for r in ctx.data.runs:
            skips.update({k.split(":", 1)[1]: v for k, v in r.status_counts.items() if k.startswith("skipped:")})
            errors.update(r.send_errors)
        rows.append(["Batches skipped", num(sum(skips.values())), ", ".join(f"{v} {k}" for k, v in sorted(skips.items())) or DASH])
        rows.append(["Probes with a send error", num(sum(errors.values())), ", ".join(f"{v} “{k}”" for k, v in sorted(errors.items(), key=lambda kv: (-kv[1], kv[0]))[:5]) or DASH])
        probes = ctx.data.probes
        if not probes.empty:
            failed = probes["status_err"].fillna(False).astype(bool)
            rows.append(["Probes that landed with a transaction error", num(int(failed.sum())), DASH])
    inc = ctx.r("inclusion")
    if inc.get("n_sent_stale_blockhash"):
        rows.append([
            "Probes signed with an expired blockhash",
            num(inc.get("n_sent_stale_blockhash")),
            f"{num(inc.get('n_landed_stale_blockhash'))} of them landed; the HTTP endpoint serving blockhashes fell behind the tip, so whole batches could not land. Excluded from the landing statistics",
        ])
    if inc.get("spill_by_family"):
        k = sum(int(r["k"]) for r in inc["spill_by_family"])
        n = sum(int(r["n"]) for r in inc["spill_by_family"])
        rows.append(["Landed probes in another leader's block (spills)", num(k), f"{pct(k / n) if n else DASH} of {num(n)} attributed probes; attributed to the block's producer"])
    late = sum(int(v) for r in inc.get("landing_slot", []) for s, v in (r.get("slot_offset_counts") or {}).items() if int(s) > 3)
    early = sum(int(v) for r in inc.get("landing_slot", []) for s, v in (r.get("slot_offset_counts") or {}).items() if int(s) < 0)
    if inc.get("landing_slot"):
        rows.append(["Landed before / after the targeted window", f"{num(early)} / {num(late)}", DASH])
    if "n_landed_without_block" in inc:
        rows.append(["Landed probes without a stored block", num(inc.get("n_landed_without_block")), DASH])
    cc = inc.get("client_changed")
    if cc:
        rows.append(["Client changed during the run", f"{num(cc.get('n_windows'))} windows", f"{plural(cc.get('n_probes'), 'probe')}, {plural(len(cc.get('validators') or []), 'validator')}; each label counted in its own family"])
    issues = ctx.summary.get("inputs", {}).get("issues") or []
    rows.append(["Input issues logged by the loader", num(len(issues)), "; ".join(issues[:3]) + (" …" if len(issues) > 3 else "") if issues else DASH])
    return table(["What", "Count", "Detail"], rows, "lrl")


# -- leader population and clock ----------------------------------------------------


def block_leader_types(ctx: Context) -> str:
    if ctx.data is None:
        return NO_DATA
    rows = ctx.data.leader_types()
    if not rows:
        return note("No gossip snapshot in the data directory.")
    has_stake = ctx.data.total_stake > 0
    out_rows = []
    for r in rows:
        out_rows.append(
            [
                r["family"],
                pct(r["stake_share"]) if has_stake else DASH,
                num(r["n_staked"]) if has_stake else DASH,
                num(r["n_nodes"]),
                f"{pct(r['share_tpu_differs'])} of {num(r['n_with_addresses'])}" if r["n_with_addresses"] else DASH,
                f"{r['version']} ({pct(r['version_share'])})" if r["version"] else DASH,
            ]
        )
    when = datetime.fromtimestamp(ctx.data.reference_time_us / 1e6, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ctx.data.reference_time_us else "unknown time"
    stake_note = "stake from getVoteAccounts" if has_stake else "no getVoteAccounts snapshot, so no stake"
    out = (
        table(["Client family (gossip clientId)", "Stake", "Staked validators", "Gossip nodes", "tpuQuic host differs from gossip host", "Most common version"], out_rows, "lrrrrl")
        + "\n\n"
        + note(f"Snapshot nearest the first run's start ({when}); {stake_note}; versions among staked nodes when stake is known.")
    )
    ident = ctx.data.client_identity()
    if ident:
        out += "\n\n" + table(
            ["Producer's gossip clientId", "Client declared in the block footer", "Probe blocks"],
            [[r["gossip"], r["footer"], num(r["blocks"])] for r in ident],
            "llr",
        )
        out += "\n\n" + note("Footers are self-reported by the producer (SIMD-0307 user agent) and were recorded only when the entry stream delivered them.")
    return out


def _ms_q(q: list[float] | None) -> str:
    if not q:
        return DASH
    return " / ".join(f"{v:.0f}" for v in q)


def block_clock(ctx: Context) -> str:
    if ctx.data is None:
        return NO_DATA
    c = ctx.data.clock()
    if not c["n_slots"]:
        return note("No first-shred timestamps (server_unix_us) in slots.jsonl.")
    offs = c["offsets"]
    period = c.get("period") or {}
    head = ["Slots with an exact time", "Window period p10 / p50 / p90 (ms)"] + [f"Slot {o['slot'] + 1} of the window starts after (median ms)" for o in offs]
    row = [num(c["n_slots"]), f"{_ms_q(period.get('q_ms'))} (n={num(period.get('n'))})"] + [
        (f"{o['median_ms']:.0f} (n={num(o['n'])})" if o["median_ms"] is not None else DASH) for o in offs
    ]
    out = table(head, [row], "r" * len(head))
    if c["send_phase"]:
        prow = [
            [
                p["anchor"],
                num(p["n"]),
                f"{p['planned_ms']:+.0f}" if p["planned_ms"] is not None else DASH,
                " / ".join(f"{v:+.0f}" for v in p["q_ms"]) if p["q_ms"] else DASH,
            ]
            for p in c["send_phase"]
        ]
        out += "\n\n" + table(["Send anchor", "Batches", "Planned first send (ms)", "Actual first send p10 / p50 / p90 (ms)"], prow, "lrrr")
    return out + "\n\n" + note(
        "Times are the node's own first-shred timestamps; windows are the 4-slot groups starting at slots divisible by 4; "
        "send phase is the first send minus the exact start of the batch's window (negative = before the window)."
    )


BLOCKS: dict[str, Callable[[Context], str]] = {
    "headline": block_headline,
    "verdicts": block_verdicts,
    "per_validator": block_per_validator,
    "predictions": block_predictions,
    "dose_response": block_dose_response,
    "ties": block_ties,
    "sweep": block_sweep,
    "inclusion": block_inclusion,
    "fingerprint": block_fingerprint,
    "controls": block_controls,
    "power": block_power,
    "entries": block_entries,
    "measurements": block_measurements,
    "leader_types": block_leader_types,
    "clock": block_clock,
    "what_went_wrong_stats": block_what_went_wrong_stats,
}


def render_blocks(ctx: Context) -> dict[str, str]:
    """Every block's Markdown, keyed by marker name."""
    return {name: fn(ctx) for name, fn in BLOCKS.items()}

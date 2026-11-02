"""Each pre-registered prediction of docs/PREDICTIONS.md, decided from the results.

One small function per prediction id. Each reads named fields (its docstring
says which), states the evidence as a number, and returns `confirmed`,
`refuted` or `inconclusive`.

Where to read: the rule card (`summary.*.rule_card`) is the most direct
evidence for how a unit orders probes queued together, equal-fee ties, and
probes spread over time, so the order predictions read it. A few predictions
need a split the results do not carry (by send anchor; who produced a block a
probe landed in early; the advertised TPU address); those come from the small,
documented `DataFacts` methods in `context.py`, computed from `data/` with the
analysis' own estimators.

Shared conventions:

* A card component counts as evidence only when its rule beats random
  (`rule` is not `random`); `random` gives `inconclusive`, never `refuted`.
* A pair rate is read through its 95% bootstrap interval over batches: it
  *favours* a direction when the whole interval is on that side of 50% and is
  undecided when the interval contains 50%. A rate from fewer than
  `MIN_BATCHES` batches counts as missing.
* When a prediction names several families, every family with data must
  agree for `confirmed`; one family that contradicts it makes it `refuted`;
  anything else (undecided, or no family with data) is `inconclusive`.
  Families without data are listed in the evidence and otherwise ignored.
* A prediction with a secondary clause is `refuted` when the data clearly
  contradict that clause, even if the main clause holds; the evidence says which.
* Missing results (a file, a family, a field) always give `inconclusive`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scipy.stats import chi2_contingency, norm

from ..constants import BATCH_PASS_MAX_MS
from .context import Context
from .fmt import card_part, is_num, num, pct, pct_ci, pval, rate_ci

CONFIRMED = "confirmed"
REFUTED = "refuted"
INCONCLUSIVE = "inconclusive"
OUTCOMES = (CONFIRMED, REFUTED, INCONCLUSIVE)

MIN_BATCHES = 3
LOCAL = ("Agave", "JitoLabs")
FIREDANCER = ("Firedancer",)
BAM = ("AgaveBam", "FireBAM")
HARMONIC = ("HarmonicAgave", "HarmonicFiredancer")
RAKURAI = ("Rakurai",)

# Strict priority order (P-F1): at least this share of pairs.
STRICT = 0.95
# BAM batch length (P-B1) and Harmonic double-buffered window (P-H1), ms.
BAM_W_MS = (20.0, 50.0)
HARMONIC_W_MS = (40.0, 75.0)
# Two batched w estimates within this many ms count as the same (P-B4).
SAME_W_MS = 10.0
# P0: share of BAM/Harmonic windows whose tpuQuic must be a proxy.
PROXY_CONFIRM, PROXY_REFUTE = 0.9, 0.5
# P-B3: landed BAM-targeted probes needed before "never seen" counts as refuted.
B3_MIN_PROBES = 100
# P-F4: probes per anchor group needed for the landing comparison.
F4_MIN_PROBES = 20


@dataclass(frozen=True)
class Outcome:
    id: str
    prediction: str
    outcome: str
    evidence: str


@dataclass(frozen=True)
class Rule:
    id: str
    prediction: str
    fn: Callable[[Context], tuple[str, str]]

    def __call__(self, ctx: Context) -> Outcome:
        try:
            outcome, evidence = self.fn(ctx)
        except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError) as exc:
            outcome, evidence = INCONCLUSIVE, f"results incomplete ({type(exc).__name__})"
        return Outcome(self.id, self.prediction, outcome, evidence)


RULES: dict[str, Rule] = {}


def rule(pid: str, prediction: str) -> Callable[[Callable[[Context], tuple[str, str]]], Callable[[Context], tuple[str, str]]]:
    def register(fn: Callable[[Context], tuple[str, str]]) -> Callable[[Context], tuple[str, str]]:
        RULES[pid] = Rule(pid, prediction, fn)
        return fn

    return register


def evaluate(ctx: Context) -> list[Outcome]:
    """Every prediction, in the order of docs/PREDICTIONS.md."""
    return [r(ctx) for r in RULES.values()]


def ids_in_document(path: Path | str) -> list[str]:
    """Prediction ids as they appear (bold) in docs/PREDICTIONS.md, in order."""
    text = Path(path).read_text(encoding="utf-8")
    seen: list[str] = []
    for m in re.finditer(r"\*\*(P0|P-[A-Z]+\d+)\b", text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def outcome_counts(outcomes: list[Outcome]) -> dict[str, int]:
    return {o: sum(1 for x in outcomes if x.outcome == o) for o in OUTCOMES}


# -- reading helpers ------------------------------------------------------------------


def _usable(rate: dict[str, Any] | None) -> bool:
    return bool(rate) and is_num(rate.get("rate")) and int(rate.get("n_batches") or 0) >= MIN_BATCHES and all(is_num(v) for v in (rate.get("ci") or [None, None]))


def _side(rate: dict[str, Any] | None) -> str | None:
    """'above', 'below' or 'straddles' 50%, or None when the rate is missing."""
    if not _usable(rate):
        return None
    lo, hi = (float(v) for v in rate["ci"])
    if lo > 0.5:
        return "above"
    if hi < 0.5:
        return "below"
    return "straddles"


def _combine(calls: list[tuple[str, str | None]]) -> str:
    """Per-family calls ('confirmed' / 'refuted' / 'inconclusive' / None = no data) → one outcome."""
    have = [c for _, c in calls if c is not None]
    if not have:
        return INCONCLUSIVE
    if REFUTED in have:
        return REFUTED
    if all(c == CONFIRMED for c in have):
        return CONFIRMED
    return INCONCLUSIVE


def _component(ctx: Context, family: str, name: str) -> dict[str, Any] | None:
    """`summary.families[family].rule_card[name]`."""
    comp = (ctx.card(family=family) or {}).get(name)
    return comp if isinstance(comp, dict) else None


def _is_pass(comp: dict[str, Any]) -> bool:
    """A batched sweep rule whose w is short enough to be the scheduler's own passes."""
    w = (comp.get("batched") or {}).get("w_ms")
    return comp.get("rule") == "batched" and is_num(w) and float(w) <= BATCH_PASS_MAX_MS


def _card_call(ctx: Context, families: tuple[str, ...], name: str, want: Callable[[dict[str, Any]], bool]) -> tuple[str, str]:
    """One card component per family: confirmed when `want(comp)`, refuted when another rule
    beats random, inconclusive when no rule does; evidence is the component's own summary.
    """
    calls, parts = [], []
    for fam in families:
        comp = _component(ctx, fam, name)
        if not comp or comp.get("rule") is None:
            calls.append((fam, None))
            parts.append(f"{fam} no data")
            continue
        label, detail = card_part(name, comp)
        parts.append(f"{fam} {label}" + (f" ({detail})" if detail else ""))
        if comp["rule"] == "random":
            calls.append((fam, INCONCLUSIVE))
        else:
            calls.append((fam, CONFIRMED if want(comp) else REFUTED))
    return _combine(calls), "; ".join(parts)


def _harmonic_cards(ctx: Context) -> list[tuple[str, str, dict[str, Any]]]:
    """(validator, family, rule card) for validators whose family is Harmonic."""
    out = []
    for v, s in sorted(ctx.validators().items()):
        card = s.get("rule_card")
        if s.get("family") in HARMONIC and isinstance(card, dict):
            out.append((v, str(s.get("family")), card))
    return out


def _mix(cards: list[tuple[str, str, dict[str, Any]]], name: str) -> str:
    counts: dict[str, int] = {}
    for _, _, card in cards:
        label = card_part(name, card.get(name))[0]
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# -- P0 -------------------------------------------------------------------------------


@rule("P0", "BAM and Harmonic: the gossip tpuQuic is a proxy, so we measure the external orderer")
def p0(ctx: Context) -> tuple[str, str]:
    """Reads `windows.jsonl: tpu_is_proxy` per planned family (data; `DataFacts.proxy_by_family`).

    Confirmed when at least 90% of BAM and Harmonic windows advertised a proxy
    tpuQuic; refuted below 50%.
    """
    if ctx.data is None or not ctx.data.proxy_by_family:
        return INCONCLUSIVE, "no window records with tpu_is_proxy"
    counts = ctx.data.proxy_by_family
    ext = [counts[f] for f in BAM + HARMONIC if f in counts]
    other = [v for f, v in counts.items() if f not in BAM + HARMONIC]
    k, n = sum(c[0] for c in ext), sum(c[1] for c in ext)
    if n == 0:
        return INCONCLUSIVE, "no BAM or Harmonic window"
    share = k / n
    ko, no = sum(c[0] for c in other), sum(c[1] for c in other)
    evidence = f"proxy tpuQuic in {pct(share)} of {num(n)} BAM/Harmonic windows"
    if no:
        evidence += f" (other families {pct(ko / no)} of {num(no)})"
    outcome = CONFIRMED if share >= PROXY_CONFIRM else (REFUTED if share < PROXY_REFUTE else INCONCLUSIVE)
    return outcome, evidence


# -- Agave and JitoLabs ---------------------------------------------------------------


@rule("P-A1", "Agave/JitoLabs ladder queued before the window: priority order")
def p_a1(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.simultaneous` of Agave and JitoLabs (rule selected on same-entry
    groups where entries exist), plus the before-window ladder rate from data
    (`DataFacts.ladder_priority_by_anchor`, anchor `before_window`).

    Confirmed when the card's rule for probes queued together is priority.
    """
    out, ev = _card_call(ctx, LOCAL, "simultaneous", lambda c: c["rule"] == "priority")
    if ctx.data is not None:
        pre = ctx.data.ladder_priority_by_anchor(LOCAL, "before_window")
        if pre and pre.get("n_pairs"):
            ev += f"; ladders sent before the window: {rate_ci(pre, with_n=True)} higher priority first"
    return out, ev


@rule("P-A2", "Agave/JitoLabs: the shared arm holds priority order more strictly than distinct")
def p_a2(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.conflicts.ladder.shared_minus_distinct` (value, ci) of Agave and
    JitoLabs: concordance with the card's simultaneous rule, shared minus distinct arm,
    ladder batches only. Requires that rule to be priority.

    Confirmed when the interval is above 0; refuted when it is below 0.
    """
    calls, parts = [], []
    for fam in LOCAL:
        card = ctx.card(family=fam) or {}
        diff = ((card.get("conflicts") or {}).get("ladder") or {}).get("shared_minus_distinct") or {}
        ci = diff.get("ci") or [None, None]
        if not card or not is_num(diff.get("value")) or not all(is_num(v) for v in ci):
            calls.append((fam, None))
            parts.append(f"{fam} no data")
            continue
        sim_rule = (card.get("simultaneous") or {}).get("closest_rule")
        lo, hi = float(ci[0]), float(ci[1])
        if sim_rule != "priority":
            call = INCONCLUSIVE
        elif lo > 0:
            call = CONFIRMED
        elif hi < 0:
            call = REFUTED
        else:
            call = INCONCLUSIVE
        calls.append((fam, call))
        parts.append(f"{fam} {float(diff['value']) * 100:+.1f} pts [{lo * 100:+.1f}, {hi * 100:+.1f}]")
    return _combine(calls), "shared − distinct concordance with priority, ladders: " + "; ".join(parts)


@rule("P-A3", "Agave/JitoLabs tie: oldest first (FIFO) at 2 and 5 ms; no reliable order at 0.5 ms")
def p_a3(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.ties` (rule, earlier_sent_first) and
    `rule_card.ties.by_spacing["0.5ms"|"2ms"|"5ms"].all` of Agave and JitoLabs.

    Main clause: the ties rule is oldest first (fifo); refuted if newest first, or
    if the 2 or 5 ms rate clearly favours newest first. Secondary clause: refuted
    when the 0.5 ms rate clearly favours one order (whole interval off 50%).
    """
    calls, parts = [], []
    for fam in LOCAL:
        ties = _component(ctx, fam, "ties")
        if not ties or ties.get("rule") is None:
            calls.append((fam, None))
            parts.append(f"{fam} no data")
            continue
        by = ties.get("by_spacing") or {}
        cell = {sp: (by.get(sp) or {}).get("all") for sp in ("0.5ms", "2ms", "5ms")}
        call = {"fifo": CONFIRMED, "lifo": REFUTED}.get(ties["rule"], INCONCLUSIVE)
        if any(_side(cell[sp]) == "below" for sp in ("2ms", "5ms")):
            call = REFUTED
        fast = _side(cell["0.5ms"])
        fast_ordered = call == CONFIRMED and fast in ("above", "below")
        if fast_ordered:
            call = REFUTED
        calls.append((fam, call))
        label, detail = card_part("ties", ties)
        spacing = ", ".join(f"{sp} {rate_ci(cell[sp])} ({num((cell[sp] or {}).get('n_batches'))} batches)" for sp in ("0.5ms", "2ms", "5ms") if cell[sp])
        parts.append(f"{fam} {label} ({detail}); earlier sent first by spacing: {spacing or 'no data'}" + ("; 0.5 ms is reliably ordered too" if fast_ordered else ""))
    return _combine(calls), "; ".join(parts)


@rule("P-A4", "Agave/JitoLabs ladder arriving mid-window: weaker priority order than P-A1")
def p_a4(ctx: Context) -> tuple[str, str]:
    """Reads ladder pairs of Agave and JitoLabs blocks from data, split by the batch's
    send anchor (`design.json` anchor type): `DataFacts.ladder_priority_by_anchor` and
    `DataFacts.ladder_anchor_difference` (before minus in-window, batch bootstrap).

    Confirmed when the difference interval is above 0; refuted when below 0.
    """
    if ctx.data is None:
        return INCONCLUSIVE, "no data directory"
    pre = ctx.data.ladder_priority_by_anchor(LOCAL, "before_window")
    mid = ctx.data.ladder_priority_by_anchor(LOCAL, "in_window")
    head = f"higher priority first, ladder: before the window {rate_ci(pre, with_n=True)}; in the window {rate_ci(mid, with_n=True)}"
    if not _usable(pre) or not _usable(mid):
        return INCONCLUSIVE, f"{head} (needs {MIN_BATCHES}+ batches each)"
    diff = ctx.data.ladder_anchor_difference(LOCAL)
    lo, hi = diff["ci"]
    if not is_num(lo) or not is_num(hi):
        return INCONCLUSIVE, head
    evidence = f"{head}; difference {diff['value'] * 100:+.1f} pts [{lo * 100:+.1f}, {hi * 100:+.1f}]"
    return (CONFIRMED if lo > 0 else (REFUTED if hi < 0 else INCONCLUSIVE)), evidence


@rule("P-A5", "Agave/JitoLabs sweep (10–20 ms apart): send order")
def p_a5(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.over_time` of Agave and JitoLabs (rule, earlier/higher-priority
    rates, `batched.w_ms`).

    Confirmed when the sweep rule is send order (fifo), or passes of at most
    `BATCH_PASS_MAX_MS` (each probe in its own pass); refuted for fee order or a
    longer batch.
    """
    return _card_call(ctx, LOCAL, "over_time", lambda c: c["rule"] == "fifo" or _is_pass(c))


@rule("P-A6", "Agave/JitoLabs: inclusion does not depend on fee")
def p_a6(ctx: Context) -> tuple[str, str]:
    """Reads `inclusion.by_family_kind_path_fee` rows with target_family Agave or
    JitoLabs, kind `ladder`, path `quic` (k landed of n sent per fee level).

    χ² test of homogeneity across fee levels: confirmed at p ≥ 0.05, refuted
    below. The lowest- and highest-fee landing rates are quoted.
    """
    rows = [
        r
        for r in ctx.r("inclusion").get("by_family_kind_path_fee", [])
        if r.get("target_family") in LOCAL and r.get("kind") == "ladder" and r.get("path") == "quic"
    ]
    by_fee: dict[int, list[int]] = {}
    for r in rows:
        c = by_fee.setdefault(int(r["priority_fee"]), [0, 0])
        c[0] += int(r["k"])
        c[1] += int(r["n"])
    levels = sorted(f for f, (_, n) in by_fee.items() if n > 0)
    if len(levels) < 2:
        return INCONCLUSIVE, "fewer than two fee levels with sent ladder probes"
    table = [[by_fee[f][0], by_fee[f][1] - by_fee[f][0]] for f in levels]
    lo_fee, hi_fee = levels[0], levels[-1]
    lo_rate = by_fee[lo_fee][0] / by_fee[lo_fee][1]
    hi_rate = by_fee[hi_fee][0] / by_fee[hi_fee][1]
    if all(row[1] == 0 for row in table) or all(row[0] == 0 for row in table):
        p = 1.0
    else:
        p = float(chi2_contingency(table)[1])
    evidence = f"landed {pct(lo_rate)} at fee {num(lo_fee)} vs {pct(hi_rate)} at {num(hi_fee)} lamports; χ² p = {pval(p)} over {len(levels)} fee levels"
    return (CONFIRMED if p >= 0.05 else REFUTED), evidence


# -- Firedancer -----------------------------------------------------------------------


@rule("P-F1", "Firedancer ladder: strict priority order")
def p_f1(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.simultaneous` of Firedancer (rule, concordance, concordance_ci).

    Strict means at least 95% of pairs in priority order: confirmed when the rule
    is priority with concordance ≥ 95%; refuted when the rule is send order, or
    priority with the whole interval below 95%.
    """
    comp = _component(ctx, "Firedancer", "simultaneous")
    if not comp or comp.get("rule") is None:
        return INCONCLUSIVE, "Firedancer: no data"
    label, detail = card_part("simultaneous", comp)
    conc, ci = comp.get("concordance"), comp.get("concordance_ci") or [None, None]
    evidence = f"Firedancer {label} ({detail}); concordance {pct(conc)} {pct_ci(ci)} (strict = at least {pct(STRICT, 0)})"
    if comp["rule"] == "random":
        return INCONCLUSIVE, evidence
    if comp["rule"] != "priority":
        return REFUTED, evidence
    if is_num(conc) and conc >= STRICT:
        return CONFIRMED, evidence
    if is_num(ci[1]) and ci[1] < STRICT:
        return REFUTED, evidence
    return INCONCLUSIVE, evidence


@rule("P-F2", "Firedancer tie: newest first (LIFO) when queued together")
def p_f2(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.ties` of Firedancer (rule, earlier_sent_first).

    Confirmed when the ties rule is newest first (lifo); refuted when oldest first.
    """
    return _card_call(ctx, FIREDANCER, "ties", lambda c: c["rule"] == "lifo")


@rule("P-F3", "Firedancer sweep: send order")
def p_f3(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.over_time` of Firedancer; confirmed when the sweep rule is send
    order (fifo) or passes of at most `BATCH_PASS_MAX_MS`.
    """
    return _card_call(ctx, FIREDANCER, "over_time", lambda c: c["rule"] == "fifo" or _is_pass(c))


@rule("P-F4", "Firedancer: pre-window sends are kept until the blockhash expires")
def p_f4(ctx: Context) -> tuple[str, str]:
    """Reads landing of Firedancer-targeted probes by send anchor from data
    (`DataFacts.landing_by_anchor`: `before_window` vs `in_window`).

    Refuted when pre-window probes land significantly less often than in-window
    ones (one-sided two-proportion z-test, p < 0.05); confirmed otherwise.
    """
    if ctx.data is None:
        return INCONCLUSIVE, "no data directory"
    by = ctx.data.landing_by_anchor("Firedancer")
    pre, mid = by.get("before_window") or {"k": 0, "n": 0}, by.get("in_window") or {"k": 0, "n": 0}
    k1, n1, k2, n2 = pre["k"], pre["n"], mid["k"], mid["n"]
    if n1 < F4_MIN_PROBES or n2 < F4_MIN_PROBES:
        return INCONCLUSIVE, f"Firedancer: {num(n1)} probes sent before the window and {num(n2)} in it"
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2)) if 0 < pooled < 1 else 0.0
    p = float(norm.cdf((p1 - p2) / se)) if se > 0 else (0.0 if p1 < p2 else 1.0)
    evidence = f"landed {pct(p1)} of {num(n1)} sent before the window vs {pct(p2)} of {num(n2)} sent in it; one-sided p = {pval(p)}"
    return (REFUTED if p < 0.05 else CONFIRMED), evidence


# -- BAM ------------------------------------------------------------------------------


@rule("P-B1", "BAM sweep: batch auction, batched(w) with w between 20 and 50 ms")
def p_b1(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.over_time` of AgaveBam and FireBAM: rule, `batched.w_ms`,
    `batched.w_range_ms`, `batched.w_identifiable`.

    Confirmed when the sweep rule is batched with a w range that meets 20–50 ms;
    refuted for another rule or a w range entirely outside; inconclusive when w
    is not identifiable on the data.
    """
    calls, parts = [], []
    for fam in BAM:
        comp = _component(ctx, fam, "over_time")
        if not comp or comp.get("rule") is None:
            calls.append((fam, None))
            parts.append(f"{fam} no data")
            continue
        label, detail = card_part("over_time", comp)
        parts.append(f"{fam} {label}" + (f" ({detail})" if detail else ""))
        if comp["rule"] == "random":
            calls.append((fam, INCONCLUSIVE))
            continue
        if comp["rule"] != "batched":
            calls.append((fam, REFUTED))
            continue
        b = comp.get("batched") or {}
        rng = b.get("w_range_ms") or [b.get("w_ms"), b.get("w_ms")]
        if not b.get("w_identifiable", True):
            calls.append((fam, INCONCLUSIVE))
        elif is_num(rng[0]) and is_num(rng[1]) and rng[1] >= BAM_W_MS[0] and rng[0] <= BAM_W_MS[1]:
            calls.append((fam, CONFIRMED))
        else:
            calls.append((fam, REFUTED))
    return _combine(calls), "sweep: " + "; ".join(parts)


@rule("P-B2", "BAM ladder burst: priority order")
def p_b2(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.simultaneous` of AgaveBam and FireBAM; confirmed when the rule
    for probes queued together is priority.
    """
    return _card_call(ctx, BAM, "simultaneous", lambda c: c["rule"] == "priority")


@rule("P-B3", "BAM: pre-window sends can land in the previous BAM leader's block")
def p_b3(ctx: Context) -> tuple[str, str]:
    """Reads probes aimed at AgaveBam/FireBAM leaders that landed before their window,
    and the family of each such block's producer, from data (`DataFacts.early_landings`).

    Confirmed when at least one landed in a block produced by a BAM leader;
    refuted when none did among at least 100 landed BAM-targeted probes.
    """
    if ctx.data is None:
        return INCONCLUSIVE, "no data directory"
    e = ctx.data.early_landings(BAM)
    in_bam = sum(n for fam, n in e["producers"].items() if fam in BAM)
    who = ", ".join(f"{n} {fam}" for fam, n in e["producers"].items()) or "none"
    evidence = f"{num(e['n_early'])} of {num(e['n_landed'])} landed BAM-targeted probes landed before the window; their producers: {who}"
    if in_bam > 0:
        return CONFIRMED, evidence
    return (REFUTED if e["n_landed"] >= B3_MIN_PROBES else INCONCLUSIVE), evidence


@rule("P-B4", "FireBAM orders like AgaveBam")
def p_b4(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.simultaneous`, `.ties` and `.over_time` (rule, `batched.w_ms`) of
    AgaveBam and FireBAM.

    Compared part by part where both detect a rule: confirmed when every such
    part agrees (batched within `SAME_W_MS` counts as agreeing); refuted when any
    differs; inconclusive when no part is detected in both.
    """
    a, f = ctx.card(family="AgaveBam"), ctx.card(family="FireBAM")
    if not a or not f:
        return INCONCLUSIVE, "one of the BAM families has no data"
    agree, differ, parts = 0, 0, []
    for name in ("simultaneous", "ties", "over_time"):
        ca, cf = a.get(name) or {}, f.get(name) or {}
        ra, rf = ca.get("rule"), cf.get("rule")
        parts.append(f"{name.replace('_', ' ')}: AgaveBam {card_part(name, ca)[0]}, FireBAM {card_part(name, cf)[0]}")
        if ra in (None, "random") or rf in (None, "random"):
            continue
        same = ra == rf
        if same and ra == "batched":
            wa, wf = (ca.get("batched") or {}).get("w_ms"), (cf.get("batched") or {}).get("w_ms")
            same = is_num(wa) and is_num(wf) and abs(float(wa) - float(wf)) <= SAME_W_MS
        agree += int(same)
        differ += int(not same)
    outcome = REFUTED if differ else (CONFIRMED if agree else INCONCLUSIVE)
    return outcome, "; ".join(parts)


# -- Harmonic -------------------------------------------------------------------------


@rule("P-H1", "Harmonic validators on the default fba strategy: batched(w ≈ 50 ms)")
def p_h1(ctx: Context) -> tuple[str, str]:
    """Reads `summary.validators[v].rule_card.over_time` for Harmonic validators: rule,
    `batched.w_ms`, `batched.w_range_ms`.

    The strategy is not published, and fba is the default, so the prediction is
    about the typical Harmonic validator. Among validators whose sweep shows a
    detectable rule: confirmed when most of them are batched with a w range meeting
    40–75 ms; refuted when most show another rule (or a batch length outside the
    range); inconclusive when fewer than three validators have a detectable rule.
    """
    cards = _harmonic_cards(ctx)
    if not cards:
        return INCONCLUSIVE, "no Harmonic validator with data"
    detected = [card.get("over_time") or {} for _, _, card in cards if (card.get("over_time") or {}).get("rule") not in (None, "random")]
    batched = [c.get("batched") or {} for c in detected if c.get("rule") == "batched"]
    evidence = f"{len(cards)} Harmonic validators, over time: {_mix(cards, 'over_time')}"
    if len(detected) < 3:
        return INCONCLUSIVE, evidence + "; fewer than three validators with a detectable rule over time"

    def meets(b: dict[str, Any]) -> bool:
        rng = b.get("w_range_ms") or [b.get("w_ms"), b.get("w_ms")]
        return is_num(rng[0]) and is_num(rng[1]) and rng[1] >= HARMONIC_W_MS[0] and rng[0] <= HARMONIC_W_MS[1]

    ok = sum(1 for b in batched if meets(b))
    return (CONFIRMED if 2 * ok > len(detected) else REFUTED), evidence + f"; {ok} of {len(detected)} with a detectable rule are batched at 40–75 ms"


@rule("P-H2", "Harmonic validators on fifo: send order regardless of fee")
def p_h2(ctx: Context) -> tuple[str, str]:
    """Reads `summary.validators[v].rule_card.simultaneous` for Harmonic validators.

    A fifo-strategy validator orders even probes queued together by send time,
    whatever their fee. Confirmed when at least one Harmonic validator's rule for
    probes queued together is send order; otherwise inconclusive (the strategy is
    per validator and not published).
    """
    cards = _harmonic_cards(ctx)
    if not cards:
        return INCONCLUSIVE, "no Harmonic validator with data"
    fifo = [v for v, _, card in cards if (card.get("simultaneous") or {}).get("rule") == "fifo"]
    fifo_over = [v for v, _, card in cards if (card.get("over_time") or {}).get("rule") == "fifo"]
    evidence = (
        f"{len(cards)} Harmonic validators, queued together: {_mix(cards, 'simultaneous')}; "
        f"send order detected for {len(set(fifo) | set(fifo_over))} validator(s): {', '.join(v[:6] + '…' for v in sorted(set(fifo) | set(fifo_over))) or 'none'}"
    )
    return (CONFIRMED if fifo else INCONCLUSIVE), evidence


@rule("P-H3", "Harmonic: the validator's own client (Agave vs Firedancer) does not change the order")
def p_h3(ctx: Context) -> tuple[str, str]:
    """Reads `summary.validators[v].rule_card` for Harmonic validators: the detected rules
    for probes queued together and over time, as one label per validator.

    Each validator runs its own strategy, so the clients are compared on the labels
    their validators show: confirmed when some label appears under both clients;
    refuted when both clients have labelled validators and none is shared.
    """
    labels: dict[str, set[str]] = {f: set() for f in HARMONIC}
    for _, fam, card in _harmonic_cards(ctx):
        sim = (card.get("simultaneous") or {}).get("rule")
        over = card.get("over_time") or {}
        if sim in (None, "random") or over.get("rule") in (None, "random"):
            continue
        w = (over.get("batched") or {}).get("w_ms")
        o = f"batched({float(w):g} ms)" if over["rule"] == "batched" and is_num(w) else str(over["rule"])
        labels[fam].add(f"{sim}/{o}")
    evidence = "; ".join(f"{fam} {', '.join(sorted(labels[fam])) or 'no detected label'}" for fam in HARMONIC) + " (queued together / over time)"
    if not labels[HARMONIC[0]] or not labels[HARMONIC[1]]:
        return INCONCLUSIVE, evidence
    return (CONFIRMED if labels[HARMONIC[0]] & labels[HARMONIC[1]] else REFUTED), evidence


# -- Rakurai and format ---------------------------------------------------------------


@rule("P-R1", "Rakurai ladder: priority order")
def p_r1(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.simultaneous` of Rakurai; confirmed when the rule for probes
    queued together is priority.
    """
    return _card_call(ctx, RAKURAI, "simultaneous", lambda c: c["rule"] == "priority")


@rule("P-X1", "Agave and Firedancer: no order difference between v1 and legacy at equal fee")
def p_x1(ctx: Context) -> tuple[str, str]:
    """Reads `rule_card.format` (rate of v1 first at equal fee, ci, p_perm, detectable) of
    Agave and Firedancer.

    Confirmed when the v1/legacy difference is not detectable (p ≥ alpha) for every
    family with format pairs; refuted when it is. BAM and Harmonic rates are quoted
    as the open question they are, without entering the outcome.
    """
    calls, parts = [], []
    for fam in ("Agave", "Firedancer"):
        fmt = (ctx.card(family=fam) or {}).get("format") or {}
        if not fmt.get("n_pairs"):
            calls.append((fam, None))
            parts.append(f"{fam} no data")
            continue
        calls.append((fam, REFUTED if fmt.get("detectable") else CONFIRMED))
        parts.append(f"{fam} {rate_ci(fmt, with_n=True)}, p = {pval(fmt.get('p_perm'))}")
    others = []
    for fam in BAM + HARMONIC:
        fmt = (ctx.card(family=fam) or {}).get("format") or {}
        if fmt.get("n_pairs"):
            others.append(f"{fam} {rate_ci(fmt)}")
    evidence = "v1 first at equal fee: " + "; ".join(parts)
    if others:
        evidence += "; open question: " + "; ".join(others)
    return _combine(calls), evidence


__all__ = ["RULES", "Outcome", "evaluate", "ids_in_document", "outcome_counts", "OUTCOMES", "CONFIRMED", "REFUTED", "INCONCLUSIVE"]

"""Each pre-registered prediction of docs/PREDICTIONS.md, decided from the results.

One small function per prediction id. Each reads named fields of the results
JSON (its docstring says which), states the evidence as a number, and returns
`confirmed`, `refuted` or `inconclusive`.

Shared conventions:

* A pair rate is read through its 95% bootstrap interval over batches. It
  *favours* the predicted direction when the whole interval is above 50%,
  *opposes* it when the whole interval is below 50%, and is undecided when
  the interval contains 50%.
* A rate built from fewer than `MIN_BATCHES` batches counts as missing.
* When a prediction names several families, every family with data must
  agree for `confirmed`; one family that contradicts it makes it `refuted`;
  anything else (undecided, or no family with data) is `inconclusive`.
  Families without data are listed in the evidence and otherwise ignored.
* Missing results (a file, a family, a field) always give `inconclusive`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scipy.stats import chi2_contingency, norm

from .context import Context
from .fmt import DASH, is_num, model_text, num, pct, pval, rate_ci, rule_text

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

# Strict priority order (P-F1): at least this share of ladder pairs.
STRICT = 0.95
# BAM batch length (P-B1) and Harmonic double-buffered window (P-H1), ms.
BAM_W_MS = (20.0, 50.0)
HARMONIC_W_MS = (40.0, 75.0)
# P0: share of BAM/Harmonic windows whose tpuQuic must be a proxy.
PROXY_CONFIRM, PROXY_REFUTE = 0.9, 0.5
# P-B3: landed pre-window BAM probes needed before "never seen" counts as refuted.
B3_MIN_PROBES = 100


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
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
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


# -- reading the results ----------------------------------------------------------


def _cond(ctx: Context, family: str, key: str) -> dict[str, Any] | None:
    """`pairs_by_condition.by_family[family][key]`."""
    cell = ctx.r("pairs_by_condition").get("by_family", {}).get(family, {}).get(key)
    return cell if isinstance(cell, dict) else None


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


def _directional(ctx: Context, families: tuple[str, ...], get: Callable[[str], dict[str, Any] | None], want: str, label: str) -> tuple[str, str]:
    """A rate per family that should sit `want` ('above' or 'below') 50%."""
    calls, parts = [], []
    for fam in families:
        rate = get(fam)
        side = _side(rate)
        if side is None:
            calls.append((fam, None))
            parts.append(f"{fam} no data")
            continue
        if side == want:
            calls.append((fam, CONFIRMED))
        elif side == "straddles":
            calls.append((fam, INCONCLUSIVE))
        else:
            calls.append((fam, REFUTED))
        parts.append(f"{fam} {rate_ci(rate)}")
    return _combine(calls), f"{label}: " + "; ".join(parts)


def _fam_model(ctx: Context, family: str) -> dict[str, Any] | None:
    """`models.families[family]`."""
    fit = ctx.r("models").get("families", {}).get(family)
    return fit if isinstance(fit, dict) else None


def _val_family(ctx: Context, v: str) -> str | None:
    return ctx.validators().get(v, {}).get("family")


# -- P0 -------------------------------------------------------------------------------


@rule("P0", "BAM and Harmonic: the gossip tpuQuic is a proxy, so we measure the external orderer")
def p0(ctx: Context) -> tuple[str, str]:
    """Reads `windows.jsonl: tpu_is_proxy` per planned family (data), and the
    selected family rules `summary.families[*].best_rule` for context.

    Confirmed when at least 90% of BAM and Harmonic windows advertised a
    proxy tpuQuic; refuted below 50%.
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
    """Reads `pairs_by_condition.by_family[Agave|JitoLabs]["ladder/all"].higher_priority_first`.

    The results pool ladder batches over anchors, so mid-window ladders (P-A4)
    are included; a split by anchor would sharpen this.
    """
    out, ev = _directional(ctx, LOCAL, lambda f: (_cond(ctx, f, "ladder/all") or {}).get("higher_priority_first"), "above", "higher priority first, ladder")
    return out, ev + " (all anchors)"


@rule("P-A2", "Agave/JitoLabs: the shared arm holds priority order more strictly than distinct")
def p_a2(ctx: Context) -> tuple[str, str]:
    """Reads `models.families[Agave|JitoLabs].by_arm.shared_minus_distinct` (value, ci)
    and `reference_model`.

    Confirmed when the interval of shared − distinct concordance is above 0 and
    the reference rule is a priority rule; refuted when it is below 0.
    """
    calls, parts = [], []
    for fam in LOCAL:
        fit = _fam_model(ctx, fam)
        diff = (fit or {}).get("by_arm", {}).get("shared_minus_distinct", {})
        ci = diff.get("ci") or [None, None]
        if not fit or not is_num(diff.get("value")) or not all(is_num(v) for v in ci):
            calls.append((fam, None))
            parts.append(f"{fam} no data")
            continue
        ref = str(fit.get("reference_model") or "")
        lo, hi = float(ci[0]), float(ci[1])
        if not ref.startswith("priority"):
            call = INCONCLUSIVE
        elif lo > 0:
            call = CONFIRMED
        elif hi < 0:
            call = REFUTED
        else:
            call = INCONCLUSIVE
        calls.append((fam, call))
        parts.append(f"{fam} {float(diff['value']) * 100:+.1f} pts [{lo * 100:+.1f}, {hi * 100:+.1f}] vs {model_text(ref)}")
    return _combine(calls), "shared − distinct concordance: " + "; ".join(parts)


@rule("P-A3", "Agave/JitoLabs tie: oldest first (FIFO) at 2 and 5 ms; no reliable order at 0.5 ms")
def p_a3(ctx: Context) -> tuple[str, str]:
    """Reads `summary.families[Agave|JitoLabs].key_rates.earlier_sent_first_tie`.

    The results pool the three tie spacings, so only the FIFO direction is
    tested here; the 0.5 ms part needs a per-spacing split.
    """
    out, ev = _directional(ctx, LOCAL, lambda f: (ctx.family(f) or {}).get("key_rates", {}).get("earlier_sent_first_tie"), "above", "earlier sent first, tie")
    return out, ev + " (spacings pooled)"


@rule("P-A4", "Agave/JitoLabs ladder arriving mid-window: weaker priority order than P-A1")
def p_a4(ctx: Context) -> tuple[str, str]:
    """Needs the ladder priority rate split by anchor (`before_window` vs
    `in_window`). `pairs_by_condition` strata are kind × arm only, so this stays
    inconclusive until that split exists; the pooled rate is shown.
    """
    parts = [f"{f} {rate_ci((_cond(ctx, f, 'ladder/all') or {}).get('higher_priority_first'))}" for f in LOCAL]
    return INCONCLUSIVE, "no split by send anchor in the results; pooled ladder: " + "; ".join(parts)


@rule("P-A5", "Agave/JitoLabs sweep (10–20 ms apart): send order")
def p_a5(ctx: Context) -> tuple[str, str]:
    """Reads `pairs_by_condition.by_family[Agave|JitoLabs]["sweep/all"].earlier_sent_first`.

    In a sweep the fee rises with send time, so earlier-sent-first below 50%
    means the fee won instead.
    """
    return _directional(ctx, LOCAL, lambda f: (_cond(ctx, f, "sweep/all") or {}).get("earlier_sent_first"), "above", "earlier sent first, sweep")


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
    """Reads `pairs_by_condition.by_family.Firedancer["ladder/all"].higher_priority_first`.

    Strict means at least 95% of pairs: confirmed when the rate is ≥ 95% and
    its interval is above 50%; refuted when the whole interval is below 95%.
    """
    rate = (_cond(ctx, "Firedancer", "ladder/all") or {}).get("higher_priority_first")
    if not _usable(rate):
        return INCONCLUSIVE, "Firedancer ladder: no data"
    lo, hi = (float(v) for v in rate["ci"])
    if float(rate["rate"]) >= STRICT and lo > 0.5:
        out = CONFIRMED
    elif hi < STRICT:
        out = REFUTED
    else:
        out = INCONCLUSIVE
    return out, f"higher priority first, ladder: Firedancer {rate_ci(rate, with_n=True)} (strict = at least {pct(STRICT, 0)})"


@rule("P-F2", "Firedancer tie: newest first (LIFO) when queued together")
def p_f2(ctx: Context) -> tuple[str, str]:
    """Reads `summary.families.Firedancer.key_rates.earlier_sent_first_tie`;
    LIFO means the interval is below 50%. Also quotes `best_model`.
    """
    out, ev = _directional(ctx, FIREDANCER, lambda f: (ctx.family(f) or {}).get("key_rates", {}).get("earlier_sent_first_tie"), "below", "earlier sent first, tie")
    fam = ctx.family("Firedancer")
    if fam and fam.get("best_model"):
        ev += f"; best rule {model_text(fam.get('best_model'))}"
    return out, ev


@rule("P-F3", "Firedancer sweep: send order")
def p_f3(ctx: Context) -> tuple[str, str]:
    """Reads `pairs_by_condition.by_family.Firedancer["sweep/all"].earlier_sent_first`."""
    return _directional(ctx, FIREDANCER, lambda f: (_cond(ctx, f, "sweep/all") or {}).get("earlier_sent_first"), "above", "earlier sent first, sweep")


def _landing(ctx: Context, family: str, kinds: tuple[str, ...]) -> tuple[int, int]:
    rows = [r for r in ctx.r("inclusion").get("by_family_kind", []) if r.get("target_family") == family and r.get("kind") in kinds]
    return sum(int(r["k"]) for r in rows), sum(int(r["n"]) for r in rows)


@rule("P-F4", "Firedancer: pre-window sends are kept until the blockhash expires")
def p_f4(ctx: Context) -> tuple[str, str]:
    """Reads `inclusion.by_family_kind` for target_family Firedancer.

    Tie and format batches are always sent 3 slots before the window and
    sweeps 1 slot into it (crates/precedence/src/plan.rs). Refuted when
    pre-window probes land significantly less often than in-window ones
    (one-sided two-proportion z-test, p < 0.05); confirmed otherwise.
    """
    k1, n1 = _landing(ctx, "Firedancer", ("tie", "format"))
    k2, n2 = _landing(ctx, "Firedancer", ("sweep",))
    if n1 < 20 or n2 < 20:
        return INCONCLUSIVE, f"Firedancer: {num(n1)} pre-window and {num(n2)} in-window probes sent"
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2)) if 0 < pooled < 1 else 0.0
    p = float(norm.cdf((p1 - p2) / se)) if se > 0 else (0.0 if p1 < p2 else 1.0)
    evidence = f"landed {pct(p1)} of {num(n1)} pre-window vs {pct(p2)} of {num(n2)} in-window probes; one-sided p = {pval(p)}"
    return (REFUTED if p < 0.05 else CONFIRMED), evidence


# -- BAM ------------------------------------------------------------------------------


@rule("P-B1", "BAM sweep: batch auction, batched(w) with w between 20 and 50 ms")
def p_b1(ctx: Context) -> tuple[str, str]:
    """Reads `models.families[AgaveBam|FireBAM]`: `verdict`, `best_rule`, `w_ms`.

    Confirmed when the selected rule is batched with 20 ≤ w ≤ 50 ms; refuted
    when another rule is selected, or batched with w outside that range.
    """
    calls, parts = [], []
    for fam in BAM:
        fit = _fam_model(ctx, fam)
        if not fit or fit.get("verdict") == "insufficient_data":
            calls.append((fam, None))
            parts.append(f"{fam} no data")
            continue
        if fit.get("verdict") != "rule":
            calls.append((fam, INCONCLUSIVE))
            parts.append(f"{fam} no rule detectable (p = {pval(fit.get('p_perm'))})")
            continue
        w = fit.get("w_ms")
        ok = fit.get("best_rule") == "batched" and is_num(w) and BAM_W_MS[0] <= float(w) <= BAM_W_MS[1]
        calls.append((fam, CONFIRMED if ok else REFUTED))
        parts.append(f"{fam} {rule_text(fit.get('best_rule'), w)}")
    return _combine(calls), "selected rule: " + "; ".join(parts)


@rule("P-B2", "BAM ladder burst: priority order")
def p_b2(ctx: Context) -> tuple[str, str]:
    """Reads `pairs_by_condition.by_family[AgaveBam|FireBAM]["ladder/all"].higher_priority_first`."""
    return _directional(ctx, BAM, lambda f: (_cond(ctx, f, "ladder/all") or {}).get("higher_priority_first"), "above", "higher priority first, ladder")


@rule("P-B3", "BAM: pre-window sends can land in the previous BAM leader's block")
def p_b3(ctx: Context) -> tuple[str, str]:
    """Reads `inclusion.landing_slot` rows with target_family AgaveBam or FireBAM:
    `slot_offset_counts` with a negative offset are probes that landed before
    the targeted window, i.e. in an earlier leader's block.

    Confirmed when any such probe exists; refuted when none appears among at
    least 100 landed probes; inconclusive with fewer. Whether that earlier
    leader was itself a BAM leader is not in the results.
    """
    rows = [r for r in ctx.r("inclusion").get("landing_slot", []) if r.get("target_family") in BAM]
    n = sum(int(r.get("n") or 0) for r in rows)
    early = sum(int(v) for r in rows for k, v in (r.get("slot_offset_counts") or {}).items() if int(k) < 0)
    evidence = f"{num(early)} of {num(n)} landed BAM-targeted probes landed before the window"
    if early > 0:
        return CONFIRMED, evidence
    return (REFUTED if n >= B3_MIN_PROBES else INCONCLUSIVE), evidence


@rule("P-B4", "FireBAM orders like AgaveBam")
def p_b4(ctx: Context) -> tuple[str, str]:
    """Reads `models.families[AgaveBam|FireBAM]`: `verdict`, `best_rule`, `w_ms`,
    `concordance`.

    Confirmed when both families select the same rule; refuted when both
    detect a rule and the rules differ.
    """
    a, f = _fam_model(ctx, "AgaveBam"), _fam_model(ctx, "FireBAM")
    if not a or not f:
        return INCONCLUSIVE, "one of the BAM families has no data"
    desc = "; ".join(f"{name} {rule_text(m.get('best_rule'), m.get('w_ms')) if m.get('verdict') == 'rule' else 'no rule detectable'}" for name, m in (("AgaveBam", a), ("FireBAM", f)))
    if a.get("verdict") != "rule" or f.get("verdict") != "rule":
        return INCONCLUSIVE, desc
    return (CONFIRMED if a.get("best_rule") == f.get("best_rule") else REFUTED), desc


# -- Harmonic -------------------------------------------------------------------------


def _harmonic_validators(ctx: Context) -> list[tuple[str, dict[str, Any]]]:
    """`summary.validators` whose family is HarmonicAgave or HarmonicFiredancer."""
    return [(v, s) for v, s in sorted(ctx.validators().items()) if s.get("family") in HARMONIC and s.get("verdict") != "insufficient_data"]


def _harmonic_mix(vals: list[tuple[str, dict[str, Any]]]) -> str:
    parts = []
    for _, s in vals:
        parts.append(rule_text(s.get("best_rule"), s.get("w_ms")) if s.get("verdict") == "rule" else "none detectable")
    counts: dict[str, int] = {}
    for p in parts:
        counts[p] = counts.get(p, 0) + 1
    return ", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


@rule("P-H1", "Harmonic validators on the default fba strategy: batched(w ≈ 50 ms)")
def p_h1(ctx: Context) -> tuple[str, str]:
    """Reads `summary.validators[v]` for Harmonic validators: `verdict`,
    `best_rule`, `w_ms`.

    The strategy is not published, so fba validators are the ones whose
    selected rule is batched. Confirmed when there is at least one and all
    have 40 ≤ w ≤ 75 ms; refuted when a batched validator's w is outside;
    inconclusive when none selects batched.
    """
    vals = _harmonic_validators(ctx)
    if not vals:
        return INCONCLUSIVE, "no Harmonic validator with data"
    batched = [s for _, s in vals if s.get("verdict") == "rule" and s.get("best_rule") == "batched"]
    ws = [float(s["w_ms"]) for s in batched if is_num(s.get("w_ms"))]
    evidence = f"{len(vals)} Harmonic validators: {_harmonic_mix(vals)}"
    if not batched:
        return INCONCLUSIVE, evidence
    ok = all(HARMONIC_W_MS[0] <= w <= HARMONIC_W_MS[1] for w in ws) and len(ws) == len(batched)
    return (CONFIRMED if ok else REFUTED), evidence


@rule("P-H2", "Harmonic validators on fifo: send order regardless of fee")
def p_h2(ctx: Context) -> tuple[str, str]:
    """Reads `summary.validators[v]` for Harmonic validators: `verdict`, `best_rule`.

    Confirmed when at least one Harmonic validator selects fifo (the
    strategy is per validator and not published); otherwise inconclusive.
    """
    vals = _harmonic_validators(ctx)
    if not vals:
        return INCONCLUSIVE, "no Harmonic validator with data"
    fifo = [v for v, s in vals if s.get("verdict") == "rule" and s.get("best_rule") == "fifo"]
    evidence = f"{len(vals)} Harmonic validators: {_harmonic_mix(vals)}"
    return (CONFIRMED if fifo else INCONCLUSIVE), evidence


@rule("P-H3", "Harmonic: the validator's own client (Agave vs Firedancer) does not change the order")
def p_h3(ctx: Context) -> tuple[str, str]:
    """Reads `summary.families[HarmonicAgave|HarmonicFiredancer].validator_rules`
    (selected rule per validator, counted).

    Because each validator runs its own strategy, the clients are compared
    on the rules their validators select: confirmed when some detected rule
    appears under both clients; refuted when both clients have validators
    with a detected rule and no rule is shared.
    """
    ha, hf = ctx.family("HarmonicAgave"), ctx.family("HarmonicFiredancer")
    if not ha or not hf:
        return INCONCLUSIVE, "one of the Harmonic families has no data"
    ra = {k for k, n in (ha.get("validator_rules") or {}).items() if n and k != "random"}
    rf = {k for k, n in (hf.get("validator_rules") or {}).items() if n and k != "random"}

    def show(rules: dict[str, int]) -> str:
        return ", ".join(f"{n} {rule_text(k)}" for k, n in sorted(rules.items())) or DASH

    evidence = f"HarmonicAgave {show(ha.get('validator_rules') or {})}; HarmonicFiredancer {show(hf.get('validator_rules') or {})}"
    if not ra or not rf:
        return INCONCLUSIVE, evidence
    return (CONFIRMED if ra & rf else REFUTED), evidence


# -- Rakurai and format ---------------------------------------------------------------


@rule("P-R1", "Rakurai ladder: priority order")
def p_r1(ctx: Context) -> tuple[str, str]:
    """Reads `pairs_by_condition.by_family.Rakurai["ladder/all"].higher_priority_first`."""
    return _directional(ctx, RAKURAI, lambda f: (_cond(ctx, f, "ladder/all") or {}).get("higher_priority_first"), "above", "higher priority first, ladder")


@rule("P-X1", "Agave and Firedancer: no order difference between v1 and legacy at equal fee")
def p_x1(ctx: Context) -> tuple[str, str]:
    """Reads `pairs_by_condition.format_effect[Agave|Firedancer].v1_first_same_fee`.

    Confirmed when the interval contains 50% for every family with data;
    refuted when it excludes 50%. BAM and Harmonic rates are quoted as the
    open question they are, without entering the outcome.
    """
    effect = ctx.r("pairs_by_condition").get("format_effect", {})
    calls, parts = [], []
    for fam in ("Agave", "Firedancer"):
        rate = (effect.get(fam) or {}).get("v1_first_same_fee")
        side = _side(rate)
        if side is None:
            calls.append((fam, None))
            parts.append(f"{fam} no data")
            continue
        calls.append((fam, CONFIRMED if side == "straddles" else REFUTED))
        parts.append(f"{fam} {rate_ci(rate)}")
    others = [f"{fam} {rate_ci((effect.get(fam) or {}).get('v1_first_same_fee'))}" for fam in BAM + HARMONIC if _usable((effect.get(fam) or {}).get("v1_first_same_fee"))]
    evidence = "v1 first at equal fee: " + "; ".join(parts)
    if others:
        evidence += "; open question: " + "; ".join(others)
    return _combine(calls), evidence


def outcome_counts(outcomes: list[Outcome]) -> dict[str, int]:
    return {o: sum(1 for x in outcomes if x.outcome == o) for o in OUTCOMES}


__all__ = ["RULES", "Outcome", "evaluate", "ids_in_document", "outcome_counts", "OUTCOMES", "CONFIRMED", "REFUTED", "INCONCLUSIVE"]

"""Number formatting and plain-pipe Markdown tables.

Every number in the README, REPORT and site goes through these functions, so
rounding is the same everywhere: rates as percentages with one decimal,
intervals in brackets, θ with two decimals, p-values with three.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from ..constants import THETA_MAX

DASH = "–"

RULE_SHORT = {
    "fifo": "arrival order (FIFO)",
    "priority_fifo_ties": "priority, ties oldest first",
    "priority_lifo_ties": "priority, ties newest first",
    "batched": "batch auction",
    "random": "none detectable",
}

ORDERER_CLASS_TEXT = {
    "local-greedy": "local scheduler",
    "fd-pack": "Firedancer pack",
    "bam": "BAM node",
    "harmonic": "Harmonic builder",
}


def is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def pct(x: Any, digits: int = 1) -> str:
    """0.8479 → '84.8%'."""
    if not is_num(x):
        return DASH
    return f"{float(x) * 100:.{digits}f}%"


def pct_ci(ci: Any, digits: int = 1) -> str:
    """[0.77, 0.92] → '[77.0, 92.0]' (percent, no sign)."""
    if not isinstance(ci, (list, tuple)) or len(ci) != 2 or not all(is_num(v) for v in ci):
        return ""
    lo, hi = (float(v) * 100 for v in ci)
    return f"[{lo:.{digits}f}, {hi:.{digits}f}]"


def rate_ci(rate: dict[str, Any] | None, with_n: bool = False) -> str:
    """A pair-rate record → '84.8% [77.2, 91.7]' (optionally 'n=467')."""
    if not rate or not is_num(rate.get("rate")):
        return DASH
    text = pct(rate["rate"])
    ci = pct_ci(rate.get("ci"))
    if ci:
        text += f" {ci}"
    if with_n:
        n = rate.get("n_pairs", rate.get("n"))
        if is_num(n):
            text += f" n={int(n):,}"
    return text


def num(x: Any) -> str:
    """Integers with thousands separators."""
    if not is_num(x):
        return DASH
    return f"{int(round(float(x))):,}"


def plural(n: Any, word: str, many: str | None = None) -> str:
    """'1 run', '2 runs', '1,204 blocks'."""
    if not is_num(n):
        return f"{DASH} {many or word + 's'}"
    return f"{num(n)} {word if int(n) == 1 else (many or word + 's')}"


def dec(x: Any, digits: int = 2) -> str:
    if not is_num(x):
        return DASH
    return f"{float(x):.{digits}f}"


def dec_ci(ci: Any, digits: int = 2) -> str:
    if not isinstance(ci, (list, tuple)) or len(ci) != 2 or not all(is_num(v) for v in ci):
        return ""
    return f"[{float(ci[0]):.{digits}f}, {float(ci[1]):.{digits}f}]"


def pval(p: Any) -> str:
    if not is_num(p):
        return DASH
    p = float(p)
    if p < 0.001:
        return "< 0.001"
    return f"{p:.3f}"


def ms(x: Any) -> str:
    """Milliseconds without trailing zeros: 20.0 → '20 ms', 2.5 → '2.5 ms'."""
    if not is_num(x):
        return DASH
    return f"{float(x):g} ms"


def lamports(x: Any) -> str:
    if not is_num(x):
        return DASH
    return f"{int(x):,}"


def short_key(key: Any, head: int = 4, tail: int = 4) -> str:
    """A base58 key shortened for tables: '4XJZ…46JY'."""
    if not isinstance(key, str):
        return DASH
    if len(key) <= head + tail + 1:
        return key
    return f"{key[:head]}…{key[-tail:]}"


def account_link(key: Any) -> str:
    """Markdown link to a validator identity on Solscan, shortened."""
    if not isinstance(key, str) or not key:
        return DASH
    return f"[`{short_key(key)}`](https://solscan.io/account/{key})"


def rule_text(best_rule: Any, w_ms: Any = None) -> str:
    """Readable name of a selected rule."""
    if best_rule == "batched":
        return f"batch auction, w = {ms(w_ms)}" if is_num(w_ms) else "batch auction"
    return RULE_SHORT.get(str(best_rule), str(best_rule) if best_rule else DASH)


def model_text(model_id: Any) -> str:
    """'batched(w=20ms)' → 'batch auction, w = 20 ms'; core ids → readable names."""
    if not isinstance(model_id, str):
        return DASH
    if model_id.startswith("batched(w="):
        w = model_id[len("batched(w=") :].split("ms")[0]
        return f"batch auction, w = {w} ms"
    return RULE_SHORT.get(model_id, model_id)


def strictness(unit: dict[str, Any]) -> str:
    """'88.3% [82.9, 94.5], θ 1.19 [0.87, 1.88]' for a fitted unit."""
    if not unit or not is_num(unit.get("concordance")):
        return DASH
    text = pct(unit["concordance"])
    ci = pct_ci(unit.get("concordance_ci"))
    if ci:
        text += f" {ci}"
    if is_num(unit.get("theta")):
        text += f", θ {theta(unit['theta'])}"
        tci = unit.get("theta_ci")
        if isinstance(tci, (list, tuple)) and len(tci) == 2 and all(is_num(v) for v in tci):
            text += f" [{theta(tci[0])}, {theta(tci[1])}]"
    return text


def theta(x: Any) -> str:
    """Mallows θ with two decimals; the fitting bound shows as '≥ 12'."""
    if not is_num(x):
        return DASH
    if float(x) >= THETA_MAX - 1e-9:
        return f"≥ {THETA_MAX:g}"
    return f"{float(x):.2f}"


CARD_PARTS = ("simultaneous", "ties", "over_time")
CARD_PART_TITLES = {"simultaneous": "Queued together", "ties": "Ties", "over_time": "Over time"}


def flipped(rate: dict[str, Any] | None) -> dict[str, Any] | None:
    """A pair rate read the other way round (share of pairs where the later-sent came first)."""
    if not rate or not is_num(rate.get("rate")):
        return rate
    ci = rate.get("ci") or [None, None]
    lo, hi = ci if len(ci) == 2 else (None, None)
    return {**rate, "rate": 1 - rate["rate"], "ci": [1 - hi if is_num(hi) else None, 1 - lo if is_num(lo) else None]}


def card_part(name: str, comp: dict[str, Any] | None) -> tuple[str, str]:
    """(label, detail) of one rule-card component: what decides, and the number behind it."""
    if not comp or comp.get("rule") is None:
        return ("no data", "")
    rule = comp["rule"]
    if rule == "random":
        mde = (comp.get("mde") or {}).get("concordance")
        detail = f"p = {pval(comp.get('p_perm'))}" + (f", detectable from {pct(mde)}" if is_num(mde) else "")
        return ("none detectable", detail)
    if name == "simultaneous":
        if rule == "priority":
            entries = comp.get("entries") or {}
            if comp.get("scope") == "same_entry":
                same, cross = entries.get("same_entry") or {}, entries.get("cross_entry") or {}
                detail = f"{pct(same.get('rate'))} in one entry"
                if cross.get("n_pairs"):
                    detail += f", {pct(cross.get('rate'))} across entries"
                return ("priority", detail)
            return ("priority", rate_ci(comp.get("higher_priority_first")))
        if rule == "fifo":
            return ("send order", rate_ci(comp.get("earlier_sent_first")))
    if name == "ties":
        if rule == "fifo":
            return ("oldest first", rate_ci(comp.get("earlier_sent_first")))
        if rule == "lifo":
            return ("newest first", rate_ci(flipped(comp.get("earlier_sent_first"))))
    if name == "over_time":
        if rule == "fifo":
            return ("send order", rate_ci(comp.get("earlier_sent_first")))
        if rule == "priority":
            return ("fee order", rate_ci(comp.get("higher_priority_first")))
        if rule == "batched":
            b = comp.get("batched") or {}
            kind = "scheduler passes" if b.get("reads_as") == "scheduler passes" else "batch auction"
            rng = b.get("w_range_ms") or []
            detail = f"w within {ms(rng[0])}–{ms(rng[1])}" if len(rng) == 2 and rng[0] != rng[1] else ""
            if not b.get("w_identifiable", True):
                detail = (detail + ", " if detail else "") + "w not identifiable"
            return (f"{kind}, w = {ms(b.get('w_ms'))}", detail)
    return (str(rule), "")


def card_cell(name: str, comp: dict[str, Any] | None) -> str:
    label, detail = card_part(name, comp)
    return f"{label}: {detail}" if detail else label


def gap_label(bucket: str) -> str:
    """'1-5ms' → '1–5 ms', '<1ms' → '< 1 ms', '>150ms' → '> 150 ms'."""
    text = bucket.replace("ms", " ms").replace("-", "–")
    for sign in ("<", ">"):
        if text.startswith(sign):
            text = f"{sign} {text[1:]}"
    return text


def _cell(value: Any) -> str:
    text = DASH if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return text if text.strip() else DASH


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], align: str | None = None) -> str:
    """A plain-pipe Markdown table. `align` is one letter per column: l, r or c."""
    head = [_cell(h) for h in headers]
    spec = (align or "")[: len(head)].ljust(len(head), "l")
    seps = {"l": "---", "r": "---:", "c": ":---:"}
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join(seps.get(a, "---") for a in spec) + " |"]
    for row in rows:
        cells = [_cell(v) for v in row]
        cells += [DASH] * (len(head) - len(cells))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def note(text: str) -> str:
    """An italic one-line note (used when a block has nothing to show)."""
    return f"_{text}_"

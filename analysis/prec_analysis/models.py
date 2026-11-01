"""Ordering rules as models, scored with a Mallows likelihood.

A rule maps our probes' (priority, send time) to a predicted order:

* `fifo`                 earliest sent first;
* `priority_fifo_ties`   higher priority first, equal priority oldest first;
* `priority_lifo_ties`   higher priority first, equal priority newest first;
* `batched(w, phase)`    group by floor((t − phase) / w), groups in time order,
                         priority first inside a group (ties oldest first);
* `random`               no rule: every order equally likely.

Each unit (a validator, or a family pooled over its validators) is scored
with P(π | σ, θ) ∝ exp(−θ·d_Kendall(π, σ)) on the observed orders. How
strictly a rule holds can differ by design condition (parallel threads only
reorder non-conflicting probes; send jitter only blurs closely spaced ones),
so θ ≥ 0 is fitted by maximum likelihood separately in every stratum
(kind × arm); every rule gets the same strata, so the strata never favour
one rule over another. Rules are compared by AIC: `batched` counts w and one
phase per validator (each validator's phase is the one with the smallest
Kendall distance). A batched rule is kept only if it beats the best core rule
by more than its grid search achieves on data simulated from that core rule
(parametric bootstrap). Finally the chosen rule must beat `random` in a
within-batch-block permutation test of the AIC gap, which accounts for
choosing the best of many rules; otherwise the verdict is "no rule
detectable".
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.stats import chi2

from .constants import ALPHA, BATCH_W_GRID_MS, CORE_MODELS, N_BOOT, N_PERM, PHASE_STEPS, SEED
from .mallows import SizeProfile, theta_hat_batch
from .orders import OrderSet

GUARD_SIMS = 199
READABLE = {
    "fifo": "arrival order (earliest sent first)",
    "priority_fifo_ties": "priority, equal priority oldest first",
    "priority_lifo_ties": "priority, equal priority newest first",
    "batched": "batch auction (batches in time order, priority inside)",
    "random": "no detectable rule",
}


@dataclass(frozen=True)
class RuleModel:
    name: str
    w_us: int = 0
    phase_us: int = 0

    @property
    def id(self) -> str:
        if self.name == "batched":
            return f"batched(w={self.w_us / 1000:g}ms,phase={self.phase_us / 1000:g}ms)"
        return self.name


def model_grid() -> list[RuleModel]:
    """Core rules, then batched(w, phase) for every w and phase step."""
    models = [RuleModel(n) for n in CORE_MODELS]
    for w_ms in BATCH_W_GRID_MS:
        w = w_ms * 1000
        for k in range(PHASE_STEPS):
            models.append(RuleModel("batched", w, k * w // PHASE_STEPS))
    return models


MODEL_GRID: list[RuleModel] = model_grid()
N_CORE = len(CORE_MODELS)
N_W = len(BATCH_W_GRID_MS)
CANDIDATES: list[str] = list(CORE_MODELS) + [f"batched(w={w}ms)" for w in BATCH_W_GRID_MS]


def seed_for(*parts: Any) -> int:
    """Stable 32-bit seed for a labelled computation (independent of hash randomisation)."""
    text = "|".join(str(p) for p in parts)
    return (zlib.crc32(text.encode("utf-8")) ^ SEED) & 0xFFFFFFFF


def rule_order(
    name: str,
    t: np.ndarray,
    priority: np.ndarray,
    tiebreak: np.ndarray,
    group: np.ndarray | None = None,
    w_us: int = 0,
    phase_us: int = 0,
) -> np.ndarray:
    """Indices in the order the rule puts them, optionally within `group` (sorted first)."""
    t = np.asarray(t, dtype=np.int64)
    p = np.asarray(priority, dtype=np.int64)
    r = np.asarray(tiebreak, dtype=np.int64)
    g = np.zeros(t.size, dtype=np.int64) if group is None else np.asarray(group, dtype=np.int64)
    if name == "fifo":
        keys: tuple[np.ndarray, ...] = (r, t, g)
    elif name == "priority_fifo_ties":
        keys = (r, t, -p, g)
    elif name == "priority_lifo_ties":
        keys = (-r, -t, -p, g)
    elif name == "batched":
        if w_us <= 0:
            raise ValueError("batched needs w > 0")
        batch = np.floor_divide(t - int(phase_us), int(w_us))
        keys = (r, t, -p, batch, g)
    else:
        raise ValueError(f"no deterministic order for rule {name!r}")
    return np.lexsort(keys)


def predicted_ranks(os: OrderSet, model: RuleModel) -> np.ndarray:
    order = rule_order(
        model.name,
        os.column("t_send_us", np.int64),
        os.column("agave_priority", np.int64),
        os.column("send_rank", np.int64),
        group=os.column("bb", np.int64),
        w_us=model.w_us,
        phase_us=model.phase_us,
    )
    return os.ranks_from_order(order)


def sign_matrix(os: OrderSet, models: list[RuleModel] | None = None) -> np.ndarray:
    """(n_models, n_pairs) int8: +1 where the model puts a before b."""
    models = MODEL_GRID if models is None else models
    if os.n_pairs == 0:
        return np.zeros((len(models), 0), dtype=np.int8)
    return np.stack([os.signs_from_rank(predicted_ranks(os, m)) for m in models])


def strata_of(os: OrderSet) -> tuple[np.ndarray, list[str]]:
    """Stratum code of every batch-block (kind/arm) and the stratum labels."""
    if os.n_bb == 0:
        return np.zeros(0, dtype=np.int64), []
    labels = (os.bbs["kind"].astype(str) + "/" + os.bbs["arm"].astype(str)).to_numpy()
    names = sorted(set(labels))
    lookup = {n: i for i, n in enumerate(names)}
    return np.array([lookup[x] for x in labels], dtype=np.int64), names


# -- distances ---------------------------------------------------------------


def distances(signs: np.ndarray, obs: np.ndarray, chunk: int = 256) -> np.ndarray:
    """Kendall distances: signs (M, P) against obs (P,) or (n, P) → (M,) or (M, n)."""
    s = signs.astype(np.float32)
    p = signs.shape[1]
    if obs.ndim == 1:
        return np.rint((p - s @ obs.astype(np.float32)) / 2).astype(np.int64)
    out = np.empty((signs.shape[0], obs.shape[0]), dtype=np.int64)
    for start in range(0, obs.shape[0], chunk):
        block = obs[start : start + chunk].astype(np.float32)
        out[:, start : start + chunk] = np.rint((p - s @ block.T) / 2).astype(np.int64)
    return out


def reduce_candidates(dist: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-model distances (S, M, n) → per-candidate distances (S, C, n).

    For each w the validator's phase is the one with the smallest total
    distance, chosen separately for every column (observed and each null or
    simulated data set). Returns the candidates and the chosen phase index
    per w for every column (n_w, n).
    """
    s, _, n = dist.shape
    total = dist.sum(axis=0)
    phase = total[N_CORE:].reshape(N_W, PHASE_STEPS, n).argmin(axis=1)
    grid = dist[:, N_CORE:, :].reshape(s, N_W, PHASE_STEPS, n)
    batched = np.take_along_axis(grid, phase[None, :, None, :], axis=2)[:, :, 0, :]
    return np.concatenate([dist[:, :N_CORE, :], batched], axis=1).astype(np.int32), phase


@dataclass
class Block:
    """One validator's contribution to a unit: per-stratum distances of every candidate."""

    validator: str
    bb_ids: np.ndarray
    pairs: np.ndarray
    cand: np.ndarray  # (n_strata, n_candidates, 1 + n_perm); column 0 is observed
    phase_obs: np.ndarray  # (n_w,) phase index chosen on the observed data

    @property
    def n_pairs(self) -> int:
        return int(self.pairs.size)


def _stratum_distances(
    os: OrderSet,
    signs: np.ndarray,
    strata: np.ndarray,
    n_strata: int,
    bb_ids: np.ndarray,
    draw: Any,
    n: int,
) -> np.ndarray:
    """(S, M, n) distances where `draw(bbs_in_stratum, s)` supplies (n, P_s) pair signs."""
    dist = np.zeros((n_strata, signs.shape[0], n), dtype=np.int64)
    for s in np.unique(strata[bb_ids]):
        ids = bb_ids[strata[bb_ids] == s]
        pairs = os.pairs_of(ids)
        if pairs.size == 0:
            continue
        dist[s] = distances(signs[:, pairs], draw(ids, int(s)))
    return dist


def make_block(
    os: OrderSet,
    signs: np.ndarray,
    strata: np.ndarray,
    n_strata: int,
    validator: str,
    bb_ids: np.ndarray,
    n_perm: int,
    salt: str = "",
) -> Block:
    """Observed and permutation-null candidate distances for one validator."""
    bb_ids = np.sort(np.asarray(bb_ids, dtype=np.int64))
    rng = np.random.default_rng(seed_for("perm", validator, salt))

    def draw(ids: np.ndarray, _s: int) -> np.ndarray:
        observed = os.obs[os.pairs_of(ids)][None, :]
        if n_perm <= 0:
            return observed
        return np.concatenate([observed, os.permuted_obs(rng, n_perm, ids)], axis=0)

    dist = _stratum_distances(os, signs, strata, n_strata, bb_ids, draw, 1 + max(n_perm, 0))
    cand, phase = reduce_candidates(dist)
    return Block(validator=validator, bb_ids=bb_ids, pairs=os.pairs_of(bb_ids), cand=cand, phase_obs=phase[:, 0])


def simulate_block(
    os: OrderSet,
    signs: np.ndarray,
    strata: np.ndarray,
    n_strata: int,
    block: Block,
    ranks: np.ndarray,
    theta: np.ndarray,
    n_sim: int,
    rng: np.random.Generator,
) -> Block:
    """A block of `n_sim` data sets drawn from Mallows(θ_stratum) around `ranks`."""

    def draw(ids: np.ndarray, s: int) -> np.ndarray:
        return os.mallows_obs(rng, n_sim, ids, ranks, float(theta[s]))

    dist = _stratum_distances(os, signs, strata, n_strata, block.bb_ids, draw, n_sim)
    cand, phase = reduce_candidates(dist)
    return Block(validator=block.validator, bb_ids=block.bb_ids, pairs=block.pairs, cand=cand, phase_obs=phase[:, 0])


# -- likelihood of a unit ---------------------------------------------------------


@dataclass
class UnitProfile:
    """Mallows machinery for one unit: one θ per (validator, stratum) cell.

    Cells are block-major: cell b·S + s is validator b's stratum s. Every rule
    shares the same cells, so the cells never favour one rule over another;
    they keep a sloppy validator or condition from distorting the comparison
    for the others.
    """

    cells: list[SizeProfile]
    by_stratum: list[SizeProfile]
    pooled: SizeProfile
    n_strata: int

    @classmethod
    def build(cls, os: OrderSet, strata: np.ndarray, n_strata: int, blocks: list[Block]) -> "UnitProfile":
        sizes = os.sizes
        cells = [SizeProfile(sizes[b.bb_ids[strata[b.bb_ids] == s]]) for b in blocks for s in range(n_strata)]
        bb_ids = np.concatenate([b.bb_ids for b in blocks]) if blocks else np.zeros(0, dtype=np.int64)
        by_stratum = [SizeProfile(sizes[bb_ids[strata[bb_ids] == s]]) for s in range(n_strata)]
        return cls(cells=cells, by_stratum=by_stratum, pooled=SizeProfile(sizes[bb_ids]), n_strata=n_strata)

    @property
    def present(self) -> list[int]:
        return [c for c, p in enumerate(self.cells) if p.n_pairs > 0]

    @property
    def loglik_random(self) -> float:
        return float(sum(p.loglik_random for p in self.cells))


def unit_candidates(blocks: list[Block]) -> np.ndarray:
    """Stack the validators' per-stratum candidate distances into cells: (B·S, C, n)."""
    return np.concatenate([b.cand.astype(np.int64) for b in blocks], axis=0)


def candidate_params(n_blocks: int) -> np.ndarray:
    """Rule parameters beyond θ: none for core rules; w and one phase per validator for batched."""
    return np.array([0] * N_CORE + [1 + n_blocks] * N_W, dtype=float)


def allowed_mask(allowed: tuple[int, ...] | list[int] | None) -> np.ndarray:
    """Boolean mask over CANDIDATES; None allows every candidate."""
    mask = np.ones(len(CANDIDATES), dtype=bool)
    if allowed is not None:
        mask[:] = False
        mask[list(allowed)] = True
    return mask


def aic_gap(
    d: np.ndarray, k_rule: np.ndarray, prof: UnitProfile, allowed: tuple[int, ...] | list[int] | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(T, loglik, aic) where T = AIC(random) − min over allowed rules of AIC(rule); d is (S, C, n).

    Candidates outside `allowed` get an infinite AIC.
    """
    _, c, n = d.shape
    ll = np.zeros((c, n))
    for s in prof.present:
        _, ll_s = prof.cells[s].max_loglik(d[s].ravel())
        ll += ll_s.reshape(c, n)
    k = k_rule + len(prof.present)
    aic = 2 * k[:, None] - 2 * ll
    aic[~allowed_mask(allowed)] = np.inf
    t = -2 * prof.loglik_random - aic.min(axis=0)
    return t, ll, aic


# -- fitting -----------------------------------------------------------------


@dataclass
class UnitFit:
    label: str
    n_validators: int
    n_bb: int
    n_pairs: int
    n_batches: int
    verdict: str
    best: str
    best_name: str
    reference_model: str | None
    w_ms: float | None
    phases_ms: dict[str, float]
    theta: float | None
    theta_ci: list[float | None]
    concordance: float | None
    concordance_ci: list[float | None]
    by_stratum: dict[str, Any]
    delta_aic_vs_random: float | None
    lr_stat: float | None
    p_lr_nominal: float | None
    p_perm: float | None
    indistinguishable: list[str]
    candidates: list[dict[str, Any]]
    by_arm: dict[str, Any]
    batched_check: dict[str, Any] | None = None
    t_null: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    allowed: list[int] | None = None
    best_index: int = -1
    best_phase_index: list[int] = field(default_factory=list)
    theta_cells: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))

    @property
    def selected(self) -> str:
        """The rule this unit is credited with: the chosen rule if significant, else `random`."""
        return self.best_name if self.verdict == "rule" else "random"

    def summary(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "verdict_text": verdict_text(self),
            "best_model": self.best,
            "best_rule": self.best_name,
            "reference_model": self.reference_model,
            "w_ms": self.w_ms,
            "theta": self.theta,
            "theta_ci": self.theta_ci,
            "concordance": self.concordance,
            "concordance_ci": self.concordance_ci,
            "p_perm": self.p_perm,
            "indistinguishable_from": self.indistinguishable,
            "n_batch_blocks": self.n_bb,
            "n_pairs": self.n_pairs,
        }

    def to_dict(self) -> dict[str, Any]:
        out = self.summary()
        out.update(
            {
                "label": self.label,
                "n_validators": self.n_validators,
                "n_batches": self.n_batches,
                "phase_ms": self.phases_ms,
                "by_stratum": self.by_stratum,
                "delta_aic_vs_random": self.delta_aic_vs_random,
                "lr_stat": self.lr_stat,
                "p_lr_nominal": self.p_lr_nominal,
                "candidates": self.candidates,
                "by_arm": self.by_arm,
                "batched_check": self.batched_check,
            }
        )
        return out


def candidate_name(i: int) -> str:
    return "random" if i < 0 else ("batched" if i >= N_CORE else CORE_MODELS[i])


def _fmt(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:.3f}"


def verdict_text(fit: UnitFit) -> str:
    if fit.verdict == "insufficient_data":
        return "insufficient data (no two probes of one batch in one block)"
    if fit.verdict == "no_rule_detectable":
        return f"no rule detectable (p={_fmt(fit.p_perm)}; closest rule {fit.reference_model}, concordance {_fmt(fit.concordance)})"
    rule = READABLE[fit.best_name]
    if fit.best_name == "batched":
        rule = f"batch auction w={fit.w_ms:g} ms (batches in time order, priority inside)"
    lo, hi = fit.concordance_ci
    return f"{rule}; concordance {_fmt(fit.concordance)} [{_fmt(lo)}, {_fmt(hi)}], theta {_fmt(fit.theta)}, p={_fmt(fit.p_perm)}"


def per_bb_disagreement(os: OrderSet, signs_row: np.ndarray) -> np.ndarray:
    """Kendall distance of every batch-block under one model's signs (full pair set)."""
    wrong = (signs_row != os.obs).astype(np.float64)
    return np.bincount(os.pair_bb, weights=wrong, minlength=os.n_bb)


def model_row(candidate: int, phase_index: int) -> int:
    """Row of MODEL_GRID for a candidate and (for batched) a phase index."""
    if candidate < N_CORE:
        return candidate
    return N_CORE + (candidate - N_CORE) * PHASE_STEPS + int(phase_index)


def candidate_signs(signs: np.ndarray, blocks: list[Block], candidate: int) -> np.ndarray:
    """Pair signs of a candidate, each validator with its own observed phase for batched."""
    if candidate < N_CORE:
        return signs[candidate]
    out = np.zeros(signs.shape[1], dtype=np.int8)
    for block in blocks:
        row = model_row(candidate, block.phase_obs[candidate - N_CORE])
        out[block.pairs] = signs[row, block.pairs]
    return out


def candidate_ranks(os: OrderSet, blocks: list[Block], candidate: int) -> list[np.ndarray]:
    """Predicted ranks of a candidate for every block (phase per validator for batched)."""
    if candidate < N_CORE:
        ranks = predicted_ranks(os, MODEL_GRID[candidate])
        return [ranks] * len(blocks)
    return [predicted_ranks(os, MODEL_GRID[model_row(candidate, b.phase_obs[candidate - N_CORE])]) for b in blocks]


def resample_counts(rng: np.random.Generator, n_clusters: int, n_boot: int) -> np.ndarray:
    """(n_boot, n_clusters) multiplicities of a with-replacement cluster resample."""
    draws = rng.integers(0, n_clusters, size=(n_boot, n_clusters))
    flat = (np.arange(n_boot)[:, None] * n_clusters + draws).ravel()
    return np.bincount(flat, minlength=n_boot * n_clusters).reshape(n_boot, n_clusters).astype(float)


def _cluster_bootstrap(
    rng: np.random.Generator,
    clusters: np.ndarray,
    d_bb: np.ndarray,
    sizes: np.ndarray,
    n_boot: int,
    with_theta: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Bootstrap (concordance, pooled θ) resampling batches (clusters) with replacement."""
    codes, inv = np.unique(clusters, return_inverse=True)
    c = codes.size
    pairs_bb = sizes * (sizes - 1) / 2.0
    d_c = np.bincount(inv, weights=d_bb, minlength=c)
    p_c = np.bincount(inv, weights=pairs_bb, minlength=c)
    k = resample_counts(rng, c, n_boot)
    d_s = k @ d_c
    p_s = k @ p_c
    with np.errstate(invalid="ignore", divide="ignore"):
        conc = 1.0 - d_s / p_s
    if not with_theta:
        return conc, np.full(n_boot, np.nan)
    size_values = np.unique(sizes[sizes >= 2])
    sc = np.zeros((c, size_values.size))
    for j, m in enumerate(size_values):
        sc[:, j] = np.bincount(inv, weights=(sizes == m).astype(float), minlength=c)
    return conc, theta_hat_batch(k @ sc, size_values, d_s)


def _ci(values: np.ndarray) -> list[float | None]:
    v = values[np.isfinite(values)]
    if v.size == 0:
        return [None, None]
    return [float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))]


def _cell_theta(prof: UnitProfile, d_cells: np.ndarray) -> np.ndarray:
    """θ̂ per cell for one candidate's per-cell distances (B·S,)."""
    out = np.zeros(len(prof.cells))
    for c in prof.present:
        out[c] = prof.cells[c].theta_exact(float(d_cells[c]))
    return out


def batched_check(
    os: OrderSet,
    signs: np.ndarray,
    strata: np.ndarray,
    blocks: list[Block],
    prof: UnitProfile,
    core: int,
    theta: np.ndarray,
    delta_obs: float,
    label: str,
    n_sim: int = GUARD_SIMS,
    allowed: tuple[int, ...] | list[int] | None = None,
) -> float:
    """p-value that batched beats the best core rule only through its grid search.

    Data sets are simulated from the best core rule at its fitted θ in every
    (validator, stratum) cell; on each, the best batched variant and the best
    core rule are refitted and their AIC gap recorded.
    """
    rng = np.random.default_rng(seed_for("batched-check", label))
    ranks = predicted_ranks(os, MODEL_GRID[core])
    n = prof.n_strata
    sims = [simulate_block(os, signs, strata, n, b, ranks, theta[i * n : (i + 1) * n], n_sim, rng) for i, b in enumerate(blocks)]
    _, _, aic = aic_gap(unit_candidates(sims), candidate_params(len(blocks)), prof, allowed)
    delta = aic[:N_CORE].min(axis=0) - aic[N_CORE:].min(axis=0)
    return float((1 + np.sum(delta >= delta_obs - 1e-9)) / (1 + n_sim))


def fit_unit(
    label: str,
    os: OrderSet,
    signs: np.ndarray,
    strata: np.ndarray,
    strata_names: list[str],
    blocks: list[Block],
    n_boot: int = N_BOOT,
    allowed: tuple[int, ...] | list[int] | None = None,
) -> UnitFit:
    """Select a rule for one unit (one or more validators) and quantify it.

    θ and concordance are reported for the reference rule: the chosen rule, or
    the closest non-random rule when `random` wins. `allowed` restricts the
    candidates (indices into CANDIDATES); None means all of them.
    """
    blocks = [b for b in blocks if b.n_pairs > 0]
    bb_ids = np.sort(np.concatenate([b.bb_ids for b in blocks])) if blocks else np.zeros(0, dtype=np.int64)
    n_strata = len(strata_names)
    prof = UnitProfile.build(os, strata, n_strata, blocks)
    sizes = os.sizes[bb_ids] if bb_ids.size else np.zeros(0, dtype=np.int64)
    clusters = os.bbs["cluster"].to_numpy()[bb_ids] if bb_ids.size else np.zeros(0, dtype=np.int64)
    n_batches = int(np.unique(clusters).size)
    n_pairs = prof.pooled.n_pairs
    if n_pairs == 0:
        return UnitFit(
            label=label, n_validators=len(blocks), n_bb=int(bb_ids.size), n_pairs=0, n_batches=n_batches,
            verdict="insufficient_data", best="random", best_name="random", reference_model=None, w_ms=None,
            phases_ms={}, theta=None, theta_ci=[None, None], concordance=None, concordance_ci=[None, None],
            by_stratum={}, delta_aic_vs_random=None, lr_stat=None, p_lr_nominal=None, p_perm=None,
            indistinguishable=[], candidates=[], by_arm={},
        )
    d = unit_candidates(blocks)
    k_rule = candidate_params(len(blocks))
    mask = allowed_mask(allowed)
    t, ll, aic = aic_gap(d, k_rule, prof, allowed)
    t_obs, t_null = float(t[0]), t[1:]
    p_perm = float((1 + np.sum(t_null >= t_obs - 1e-9)) / (1 + t_null.size)) if t_null.size else None
    aic_obs = aic[:, 0]
    aic_random = -2 * prof.loglik_random
    d_total = d[:, :, 0].sum(axis=0)
    ref = int(np.argmin(aic_obs))
    check: dict[str, Any] | None = None
    if ref >= N_CORE:
        core = int(np.argmin(aic_obs[:N_CORE]))
        delta_obs = float(aic_obs[core] - aic_obs[ref])
        theta_core = _cell_theta(prof, d[:, core, 0])
        p_grid = batched_check(os, signs, strata, blocks, prof, core, theta_core, delta_obs, label, allowed=allowed)
        check = {"p": p_grid, "delta_aic": delta_obs, "core_rule": CANDIDATES[core], "kept": p_grid < ALPHA}
        if p_grid >= ALPHA:
            ref = core
    best = ref if aic_obs[ref] < aic_random else -1
    best_aic = float(min(aic_obs[ref], aic_random))
    rows = [
        {
            "id": name,
            "rule": candidate_name(i),
            "n_params": int(k_rule[i] + len(prof.present)),
            "distance": int(d_total[i]),
            "theta_pooled": float(prof.pooled.theta_hat(float(d_total[i]))[0]),
            "loglik": float(ll[i, 0]),
            "aic": float(aic_obs[i]),
            "delta_aic": float(aic_obs[i] - best_aic),
            "concordance": float(1 - d_total[i] / n_pairs),
        }
        for i, name in enumerate(CANDIDATES)
        if mask[i]
    ]
    rows.append(
        {
            "id": "random", "rule": "random", "n_params": 0, "distance": None, "theta_pooled": 0.0,
            "loglik": prof.loglik_random, "aic": aic_random, "delta_aic": float(aic_random - best_aic),
            "concordance": None,
        }
    )
    best_id = "random" if best < 0 else CANDIDATES[best]
    indist = sorted(r["id"] for r in rows if r["id"] != best_id and r["delta_aic"] <= 2.0)
    significant = p_perm is not None and p_perm < ALPHA
    verdict = "rule" if (best >= 0 and significant) else "no_rule_detectable"

    s_ref = candidate_signs(signs, blocks, ref)
    d_bb = per_bb_disagreement(os, s_ref)
    rng = np.random.default_rng(seed_for("boot", label))
    conc_s, theta_s = _cluster_bootstrap(rng, clusters, d_bb[bb_ids], sizes, n_boot)
    theta_cells = _cell_theta(prof, d[:, ref, 0])
    d_stratum = d[:, ref, 0].reshape(len(blocks), n_strata).sum(axis=0)
    by_stratum = {
        strata_names[s]: {
            "n_pairs": prof.by_stratum[s].n_pairs,
            "theta": prof.by_stratum[s].theta_exact(float(d_stratum[s])),
            "concordance": float(1 - d_stratum[s] / prof.by_stratum[s].n_pairs),
        }
        for s in range(n_strata)
        if prof.by_stratum[s].n_pairs > 0
    }
    lr = float(2 * (ll[ref, 0] - prof.loglik_random))
    phases: dict[str, float] = {}
    w_ms = None
    if ref >= N_CORE:
        wi = ref - N_CORE
        w_ms = float(BATCH_W_GRID_MS[wi])
        for block in blocks:
            phases[block.validator] = float(int(block.phase_obs[wi]) * BATCH_W_GRID_MS[wi] / PHASE_STEPS)
    return UnitFit(
        label=label,
        n_validators=len(blocks),
        n_bb=int(bb_ids.size),
        n_pairs=n_pairs,
        n_batches=n_batches,
        verdict=verdict,
        best=best_id,
        best_name=candidate_name(best),
        reference_model=CANDIDATES[ref],
        w_ms=w_ms,
        phases_ms=phases,
        theta=prof.pooled.theta_exact(float(d_total[ref])),
        theta_ci=_ci(theta_s),
        concordance=float(1 - d_total[ref] / n_pairs),
        concordance_ci=_ci(conc_s),
        by_stratum=by_stratum,
        delta_aic_vs_random=float(aic_random - aic_obs[ref]),
        lr_stat=lr,
        p_lr_nominal=float(chi2.sf(lr, len(prof.present))) if lr > 0 else 1.0,
        p_perm=p_perm,
        indistinguishable=indist,
        candidates=rows,
        by_arm=arm_contrast(os, s_ref, bb_ids, label, n_boot),
        batched_check=check,
        t_null=t_null,
        allowed=None if allowed is None else sorted(int(i) for i in allowed),
        best_index=ref,
        best_phase_index=[int(b.phase_obs[ref - N_CORE]) if ref >= N_CORE else 0 for b in blocks],
        theta_cells=theta_cells,
    )


def arm_contrast(os: OrderSet, s_ref: np.ndarray, bb_ids: np.ndarray, label: str, n_boot: int) -> dict[str, Any]:
    """Concordance with the reference rule, distinct vs shared arm, and their difference.

    Shared-arm probes write-lock one account, so parallel execution threads
    cannot reorder them; shared > distinct points at the threads. Only design
    conditions (kind, anchor, path) run in both arms are compared, so the arms
    differ in nothing but the conflict.
    """
    out: dict[str, Any] = {}
    bbs = os.bbs
    arms = bbs["arm"].astype(str).to_numpy()[bb_ids]
    kinds = bbs["kind"].astype(str).to_numpy()[bb_ids]
    cond_cols = [c for c in ("kind", "anchor_type", "anchor_slots", "path") if c in bbs.columns]
    conds = bbs[cond_cols].astype(str).agg("|".join, axis=1).to_numpy()[bb_ids] if bb_ids.size else np.zeros(0, dtype=str)
    matched = sorted(c for c in set(conds) if {"distinct", "shared"} <= set(arms[conds == c]))
    keep = np.isin(conds, matched)
    both = sorted(set(kinds[keep]))
    d_bb = per_bb_disagreement(os, s_ref)
    sizes_all = os.sizes
    clusters_all = bbs["cluster"].to_numpy()
    samples: dict[str, np.ndarray] = {}
    out["kinds_compared"] = both
    out["conditions_compared"] = matched
    for arm in ("distinct", "shared"):
        ids = bb_ids[(arms == arm) & keep]
        sizes = sizes_all[ids]
        n_pairs = float((sizes * (sizes - 1) / 2).sum())
        if n_pairs == 0:
            out[arm] = {"n_pairs": 0, "concordance": None, "concordance_ci": [None, None]}
            continue
        rng = np.random.default_rng(seed_for("arm", label, arm))
        conc_s, _ = _cluster_bootstrap(rng, clusters_all[ids], d_bb[ids], sizes, n_boot, with_theta=False)
        samples[arm] = conc_s
        out[arm] = {
            "n_pairs": int(n_pairs),
            "concordance": float(1 - d_bb[ids].sum() / n_pairs),
            "concordance_ci": _ci(conc_s),
        }
    if len(samples) == 2:
        ci = _ci(samples["shared"] - samples["distinct"])
        out["shared_minus_distinct"] = {"value": out["shared"]["concordance"] - out["distinct"]["concordance"], "ci": ci}
        out["threads_reorder"] = bool(ci[0] is not None and ci[0] > 0)
    else:
        out["shared_minus_distinct"] = {"value": None, "ci": [None, None]}
        out["threads_reorder"] = None
    return out


# -- whole-dataset driver ----------------------------------------------------


@dataclass
class ModelResults:
    validators: dict[str, UnitFit]
    families: dict[str, UnitFit]
    validator_blocks: dict[str, Block]
    family_blocks: dict[str, list[Block]]
    strata: np.ndarray
    strata_names: list[str]


def fit_all(os: OrderSet, signs: np.ndarray | None = None, n_perm: int = N_PERM, n_boot: int = N_BOOT, salt: str = "") -> ModelResults:
    """Fit every validator, then every family pooled over its validators.

    A validator whose client label changed during the run contributes the
    batch-blocks it produced under each label to that label's family.
    """
    signs = sign_matrix(os) if signs is None else signs
    strata, names = strata_of(os)
    if os.n_bb == 0:
        return ModelResults({}, {}, {}, {}, strata, names)
    n_strata = len(names)
    producer = os.bbs["producer"].astype(str).to_numpy()
    family = os.bbs["producer_family"].astype(str).to_numpy()
    validator_blocks: dict[str, Block] = {}
    validators: dict[str, UnitFit] = {}
    for v in sorted(set(producer)):
        ids = np.flatnonzero(producer == v)
        validator_blocks[v] = make_block(os, signs, strata, n_strata, v, ids, n_perm, salt)
        validators[v] = fit_unit(f"validator:{v}{salt}", os, signs, strata, names, [validator_blocks[v]], n_boot)
    family_blocks: dict[str, list[Block]] = {}
    for fam in sorted(set(family)):
        blocks = []
        for v in sorted(set(producer[family == fam])):
            ids = np.flatnonzero((producer == v) & (family == fam))
            whole = validator_blocks[v]
            same = ids.size == whole.bb_ids.size
            blocks.append(whole if same else make_block(os, signs, strata, n_strata, v, ids, n_perm, f"{salt}|{fam}"))
        family_blocks[fam] = blocks
    families = {
        fam: fit_unit(f"family:{fam}{salt}", os, signs, strata, names, blocks, n_boot) for fam, blocks in family_blocks.items()
    }
    return ModelResults(validators, families, validator_blocks, family_blocks, strata, names)

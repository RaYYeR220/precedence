"""Mallows model on Kendall distance.

P(π | σ, θ) = exp(−θ·d(π, σ)) / Z(θ, n),  Z(θ, n) = ∏_{j=1..n} (1 − e^{−jθ}) / (1 − e^{−θ}).

A unit (one validator, one family) is a set of observed orders of different
sizes that share one θ. Its log-likelihood depends on the data only through
the total distance D and the multiset of sizes, which is what `SizeProfile`
captures: the MLE of θ, the profile log-likelihood and the expected
concordance at any θ, all vectorised over many D at once.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
from scipy.optimize import brentq

from .constants import THETA_MAX

_GRID = np.concatenate(([0.0], np.geomspace(1e-5, THETA_MAX, 4_000)))


def log_z(theta: np.ndarray | float, n: int) -> np.ndarray:
    """log Z(θ, n), vectorised over θ; equals log n! at θ = 0."""
    theta = np.atleast_1d(np.asarray(theta, dtype=float))
    out = np.full(theta.shape, math.lgamma(n + 1))
    pos = theta > 0
    if n <= 1 or not pos.any():
        return out if n > 1 else np.zeros(theta.shape)
    t = theta[pos][:, None]
    j = np.arange(1, n + 1, dtype=float)[None, :]
    terms = np.log(-np.expm1(-j * t)) - np.log(-np.expm1(-t))
    out[pos] = terms.sum(axis=1)
    return out


def expected_distance(theta: np.ndarray | float, n: int) -> np.ndarray:
    """E_θ[d] for orders of n items; n(n−1)/4 at θ = 0."""
    theta = np.atleast_1d(np.asarray(theta, dtype=float))
    out = np.full(theta.shape, n * (n - 1) / 4.0)
    pos = theta > 0
    if n <= 1:
        return np.zeros(theta.shape)
    if pos.any():
        t = theta[pos]
        j = np.arange(1, n + 1, dtype=float)[None, :]
        out[pos] = n / np.expm1(t) - (j / np.expm1(j * t[:, None])).sum(axis=1)
    return np.maximum(out, 0.0)


class SizeProfile:
    """Likelihood machinery for a fixed multiset of order sizes."""

    def __init__(self, sizes: np.ndarray | list[int]):
        counts = Counter(int(m) for m in np.asarray(sizes).ravel() if int(m) >= 2)
        self.counts: dict[int, int] = dict(sorted(counts.items()))
        self.n_orders = int(sum(self.counts.values()))
        self.n_pairs = int(sum(c * m * (m - 1) // 2 for m, c in self.counts.items()))
        self.loglik_random = -float(sum(c * math.lgamma(m + 1) for m, c in self.counts.items()))
        e = np.zeros_like(_GRID)
        for m, c in self.counts.items():
            e += c * expected_table(m)
        self._e_grid = e

    def total_log_z(self, theta: np.ndarray | float) -> np.ndarray:
        theta = np.atleast_1d(np.asarray(theta, dtype=float))
        out = np.zeros(theta.shape)
        for m, c in self.counts.items():
            out += c * log_z(theta, m)
        return out

    def expected_total(self, theta: np.ndarray | float) -> np.ndarray:
        theta = np.atleast_1d(np.asarray(theta, dtype=float))
        out = np.zeros(theta.shape)
        for m, c in self.counts.items():
            out += c * expected_distance(theta, m)
        return out

    def theta_hat(self, distance: np.ndarray | float) -> np.ndarray:
        """MLE of θ ≥ 0 for total distances D (interpolated on a dense grid)."""
        d = np.atleast_1d(np.asarray(distance, dtype=float))
        if self.n_pairs == 0:
            return np.zeros(d.shape)
        theta = np.interp(d, self._e_grid[::-1], _GRID[::-1])
        theta[d >= self._e_grid[0]] = 0.0
        theta[d <= self._e_grid[-1]] = THETA_MAX
        return theta

    def theta_exact(self, distance: float) -> float:
        """MLE of θ by root finding, for reported values."""
        if self.n_pairs == 0 or distance >= self._e_grid[0]:
            return 0.0
        if distance <= self._e_grid[-1]:
            return THETA_MAX
        f = lambda t: float(self.expected_total(t)[0]) - distance  # noqa: E731
        return float(brentq(f, 0.0, THETA_MAX, xtol=1e-10))

    def loglik(self, theta: np.ndarray | float, distance: np.ndarray | float) -> np.ndarray:
        theta = np.atleast_1d(np.asarray(theta, dtype=float))
        d = np.atleast_1d(np.asarray(distance, dtype=float))
        return -theta * d - self.total_log_z(theta)

    def max_loglik(self, distance: np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
        """(θ̂, max log-likelihood) for each total distance."""
        d = np.atleast_1d(np.asarray(distance, dtype=float))
        theta = self.theta_hat(d)
        return theta, self.loglik(theta, d)

    def concordance(self, distance: np.ndarray | float) -> np.ndarray:
        """1 − D / number of pairs."""
        d = np.atleast_1d(np.asarray(distance, dtype=float))
        if self.n_pairs == 0:
            return np.full(d.shape, np.nan)
        return 1.0 - d / self.n_pairs

    def expected_concordance(self, theta: np.ndarray | float) -> np.ndarray:
        if self.n_pairs == 0:
            return np.full(np.atleast_1d(theta).shape, np.nan)
        return 1.0 - self.expected_total(theta) / self.n_pairs


_E_CACHE: dict[int, np.ndarray] = {}
_COARSE = slice(None, None, 8)


def expected_table(n: int) -> np.ndarray:
    """E_θ[d | n] on the θ grid, cached."""
    if n not in _E_CACHE:
        _E_CACHE[n] = expected_distance(_GRID, n)
    return _E_CACHE[n]


def theta_hat_batch(size_counts: np.ndarray, size_values: np.ndarray, distance: np.ndarray) -> np.ndarray:
    """MLE of θ for many units at once: rows of size counts, one total distance each.

    Solves Σ_m c_m E_θ[d | m] = D on a coarse θ grid with linear interpolation
    (E is decreasing in θ); used for bootstrap distributions.
    """
    counts = np.asarray(size_counts, dtype=float)
    d = np.asarray(distance, dtype=float)
    grid = _GRID[_COARSE]
    table = np.stack([expected_table(int(m))[_COARSE] for m in np.asarray(size_values).ravel()])
    e = counts @ table  # (n_units, n_grid), decreasing along the grid
    above = (e > d[:, None]).sum(axis=1)  # first grid index with E <= D
    hi = np.clip(above, 1, grid.size - 1)
    lo = hi - 1
    rows = np.arange(d.size)
    e_lo, e_hi = e[rows, lo], e[rows, hi]
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = np.where(e_lo > e_hi, (e_lo - d) / (e_lo - e_hi), 0.0)
    theta = grid[lo] + np.clip(frac, 0.0, 1.0) * (grid[hi] - grid[lo])
    theta[d >= e[:, 0]] = 0.0
    theta[d <= e[:, -1]] = THETA_MAX
    return theta


def sample_inversion_codes(rng: np.random.Generator, theta: float, shape: tuple[int, ...], m: int) -> np.ndarray:
    """Lehmer codes of Mallows(θ) permutations of m items; shape + (m,)."""
    codes = np.zeros(shape + (m,), dtype=np.int64)
    u = rng.random(shape + (m,))
    for k in range(m):
        top = m - 1 - k
        if top == 0:
            continue
        if theta <= 0:
            codes[..., k] = np.minimum((u[..., k] * (top + 1)).astype(np.int64), top)
            continue
        q = math.exp(-theta)
        mass = -math.expm1(-theta * (top + 1))  # 1 − q^{top+1}
        r = np.floor(np.log1p(-u[..., k] * mass) / math.log(q)).astype(np.int64)
        codes[..., k] = np.clip(r, 0, top)
    return codes


def decode_lehmer(codes: np.ndarray) -> np.ndarray:
    """Permutations (values = reference positions, in observed order) from Lehmer codes."""
    m = codes.shape[-1]
    available = np.ones(codes.shape, dtype=bool)
    out = np.zeros(codes.shape, dtype=np.int64)
    for k in range(m):
        cum = np.cumsum(available, axis=-1)
        hit = available & (cum == (codes[..., k : k + 1] + 1))
        choice = hit.argmax(axis=-1)
        out[..., k] = choice
        np.put_along_axis(available, choice[..., None], False, axis=-1)
    return out


def sample_orders(rng: np.random.Generator, theta: float, shape: tuple[int, ...], m: int) -> np.ndarray:
    """Mallows(θ) samples around the identity: entry k is the reference rank placed k-th."""
    return decode_lehmer(sample_inversion_codes(rng, theta, shape, m))

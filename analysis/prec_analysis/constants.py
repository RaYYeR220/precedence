"""Constants shared with the harness and fixed analysis settings.

The fee ladders and spacings mirror `crates/precedence/src/design.rs`; the
cost arithmetic mirrors `crates/precedence/src/cost.rs`; the condition cycle
mirrors `crates/precedence/src/plan.rs`.
"""

from __future__ import annotations

LADDER8: tuple[int, ...] = (0, 10, 40, 150, 500, 1_500, 4_000, 10_000)
LADDER12: tuple[int, ...] = (0, 5, 15, 40, 100, 250, 500, 1_000, 2_000, 4_000, 7_000, 10_000)
TIE_FEE: int = 500
FORMAT_LEVELS: tuple[int, ...] = (0, 150, 1_500, 10_000)
TIE_SPACINGS_US: tuple[int, ...] = (500, 2_000, 5_000)
SWEEP_SPACINGS_US: tuple[int, ...] = (10_000, 20_000)

KINDS: tuple[str, ...] = ("ladder", "tie", "sweep", "format")
ARMS: tuple[str, ...] = ("distinct", "shared")
PATHS: tuple[str, ...] = ("quic", "rpc")
SLOTS_PER_WINDOW: int = 4

# Cost model (cost.rs).
SIGNATURE_COST: int = 720
WRITE_LOCK_COST: int = 300
INSTRUCTION_DATA_BYTES_PER_CU: int = 4
LOADED_DATA_COST_PER_32K: int = 8
BASE_FEE_PER_SIGNATURE: int = 5_000
LEADER_SHARE_PER_SIGNATURE: int = 2_500

# Client families (gossip clientId) and the component that orders their blocks.
FAMILIES: tuple[str, ...] = (
    "AgaveBam",
    "JitoLabs",
    "HarmonicAgave",
    "HarmonicFiredancer",
    "Firedancer",
    "FireBAM",
    "Rakurai",
    "Agave",
)
ORDERER_CLASS: dict[str, str] = {
    "Agave": "local-greedy",
    "JitoLabs": "local-greedy",
    "Rakurai": "local-greedy",
    "Firedancer": "fd-pack",
    "AgaveBam": "bam",
    "FireBAM": "bam",
    "HarmonicAgave": "harmonic",
    "HarmonicFiredancer": "harmonic",
}
ORDERER_CLASSES: tuple[str, ...] = ("local-greedy", "fd-pack", "bam", "harmonic")
UNKNOWN_FAMILY = "Unknown"

# Rule models.
BATCH_W_GRID_MS: tuple[int, ...] = (5, 10, 20, 30, 40, 50, 75, 100, 150, 200)
PHASE_STEPS: int = 8
CORE_MODELS: tuple[str, ...] = ("fifo", "priority_fifo_ties", "priority_lifo_ties")
MODEL_NAMES: tuple[str, ...] = CORE_MODELS + ("batched", "random")

# Send-gap buckets for the dose-response (higher-priority probe sent later), ms.
GAP_BUCKETS_MS: tuple[tuple[float, float, str], ...] = (
    (0.0, 1.0, "<1ms"),
    (1.0, 5.0, "1-5ms"),
    (5.0, 20.0, "5-20ms"),
    (20.0, 50.0, "20-50ms"),
    (50.0, 150.0, "50-150ms"),
    (150.0, float("inf"), ">150ms"),
)

# Inference settings: fixed so that every output is reproducible.
SEED: int = 20_261_101
N_BOOT: int = 2_000
N_PERM: int = 999
ALPHA: float = 0.05
POWER_TARGET: float = 0.80
POWER_SIMS: int = 200
POWER_THETAS: tuple[float, ...] = (0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0)
THETA_MAX: float = 12.0
FLOAT_DIGITS: int = 6


def leader_reward(priority_fee: int, n_sigs: int = 1) -> int:
    """Lamports the leader keeps: priority fee plus the unburned half of base fees."""
    return int(priority_fee) + LEADER_SHARE_PER_SIGNATURE * int(n_sigs)


def agave_priority(priority_fee: int, n_sigs: int, cost: int) -> int:
    """Agave's ordering key: reward per unit of cost, scaled by 1e6 (integer division)."""
    return (leader_reward(priority_fee, n_sigs) * 1_000_000) // (int(cost) + 1)


def orderer_class(family: str | None) -> str | None:
    """Orderer class of a client family, or None for families outside the study."""
    if family is None:
        return None
    return ORDERER_CLASS.get(family)

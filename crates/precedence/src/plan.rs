//! Assign batch designs to concrete leader windows.

use crate::design::{batch_seed, design_batch, design_hash, Anchor, BatchDesign, PayerArm, ProbeKind};
use crate::targets::Target;
use rand::{seq::SliceRandom, SeedableRng};
use rand_chacha::ChaCha20Rng;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Path {
    Quic,
    Rpc,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Condition {
    pub kind: ProbeKind,
    pub arm: PayerArm,
    pub anchor: Anchor,
    pub path: Path,
}

const fn c(kind: ProbeKind, arm: PayerArm, anchor: Anchor, path: Path) -> Condition {
    Condition { kind, arm, anchor, path }
}

const BEFORE: Anchor = Anchor::BeforeWindow { slots: 1.0 };
const MID: Anchor = Anchor::InWindow { slots: 1.5 };
const EARLY_IN: Anchor = Anchor::InWindow { slots: 0.5 };

/// One cycle of conditions per target (weights by repetition). Every target
/// works through the cycle in a fresh random order before repeating it.
pub const MAIN_CYCLE: [Condition; 11] = [
    c(ProbeKind::Ladder, PayerArm::Distinct, BEFORE, Path::Quic),
    c(ProbeKind::Ladder, PayerArm::Distinct, BEFORE, Path::Quic),
    c(ProbeKind::Ladder, PayerArm::Shared, BEFORE, Path::Quic),
    c(ProbeKind::Ladder, PayerArm::Shared, BEFORE, Path::Quic),
    c(ProbeKind::Ladder, PayerArm::Distinct, MID, Path::Quic),
    c(ProbeKind::Tie, PayerArm::Distinct, BEFORE, Path::Quic),
    c(ProbeKind::Tie, PayerArm::Shared, BEFORE, Path::Quic),
    c(ProbeKind::Sweep, PayerArm::Distinct, EARLY_IN, Path::Quic),
    c(ProbeKind::Sweep, PayerArm::Distinct, EARLY_IN, Path::Quic),
    c(ProbeKind::Format, PayerArm::Distinct, BEFORE, Path::Quic),
    c(ProbeKind::Ladder, PayerArm::Distinct, BEFORE, Path::Rpc),
];

/// Pilot: one batch of each kind, to check landing and timing per family.
pub const PILOT_CYCLE: [Condition; 4] = [
    c(ProbeKind::Ladder, PayerArm::Distinct, BEFORE, Path::Quic),
    c(ProbeKind::Tie, PayerArm::Distinct, BEFORE, Path::Quic),
    c(ProbeKind::Sweep, PayerArm::Distinct, EARLY_IN, Path::Quic),
    c(ProbeKind::Ladder, PayerArm::Distinct, BEFORE, Path::Rpc),
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlannedBatch {
    pub batch_id: u32,
    pub target: String,
    pub family: String,
    pub version: String,
    pub tpu_quic: String,
    pub tpu_is_proxy: bool,
    pub window_first_slot: u64,
    pub path: Path,
    pub design: BatchDesign,
    pub design_hash: String,
}

#[derive(Debug, Clone)]
pub struct PlanParams {
    pub run_seed: u64,
    pub cycle: Vec<Condition>,
    pub batches_per_target: usize,
    /// Earliest window we may use (current slot + lead time).
    pub min_slot: u64,
    /// Two batches' windows must be at least this many slots apart, so our
    /// own probes from different batches never share a block.
    pub min_gap_slots: u64,
    pub payer_pool: u16,
}

pub fn plan(targets: &[Target], p: &PlanParams) -> Vec<PlannedBatch> {
    let mut rng = ChaCha20Rng::seed_from_u64(p.run_seed);
    // Candidate (window, target) pairs, earliest first.
    let mut queue: Vec<(u64, usize)> = targets
        .iter()
        .enumerate()
        .flat_map(|(ti, t)| t.windows.iter().filter(|&&w| w >= p.min_slot).map(move |&w| (w, ti)))
        .collect();
    queue.sort();
    // Per target, a stream of conditions: shuffled cycles.
    let mut streams: Vec<Vec<Condition>> = targets
        .iter()
        .map(|_| {
            let mut s = Vec::new();
            while s.len() < p.batches_per_target {
                let mut cyc = p.cycle.clone();
                cyc.shuffle(&mut rng);
                s.extend(cyc);
            }
            s.truncate(p.batches_per_target);
            s.reverse(); // pop from the back
            s
        })
        .collect();
    let mut used: BTreeSet<u64> = BTreeSet::new();
    let mut out = Vec::new();
    for (w, ti) in queue {
        if streams[ti].is_empty() {
            continue;
        }
        let clash = used.range(w.saturating_sub(p.min_gap_slots - 1)..w + p.min_gap_slots).next().is_some();
        if clash {
            continue;
        }
        let cond = streams[ti].pop().unwrap();
        used.insert(w);
        let batch_id = out.len() as u32;
        let design = design_batch(cond.kind, cond.arm, cond.anchor, batch_seed(p.run_seed, batch_id as u64), p.payer_pool);
        let t = &targets[ti];
        out.push(PlannedBatch {
            batch_id,
            target: t.identity.clone(),
            family: t.family.clone(),
            version: t.version.clone(),
            tpu_quic: t.tpu_quic.clone(),
            tpu_is_proxy: t.tpu_is_proxy,
            window_first_slot: w,
            path: cond.path,
            design_hash: design_hash(&design),
            design,
        });
    }
    out
}

/// Hash of the whole plan, logged before the first send.
pub fn plan_hash(batches: &[PlannedBatch]) -> String {
    crate::io::sha256_hex(&serde_json::to_vec(batches).unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn target(id: &str, windows: Vec<u64>) -> Target {
        Target {
            identity: id.into(),
            family: "F".into(),
            version: "1".into(),
            stake: 1,
            tpu_quic: "1.1.1.1:1".into(),
            tpu_is_proxy: false,
            windows,
        }
    }

    fn params() -> PlanParams {
        PlanParams {
            run_seed: 9,
            cycle: MAIN_CYCLE.to_vec(),
            batches_per_target: 11,
            min_slot: 100,
            min_gap_slots: 8,
            payer_pool: 16,
        }
    }

    #[test]
    fn respects_gap_lead_and_balance() {
        let a = target("a", (0..60).map(|i| 96 + i * 8).collect());
        let b = target("b", (0..60).map(|i| 100 + i * 8).collect());
        let plan = plan(&[a, b], &params());
        let mut ws: Vec<u64> = plan.iter().map(|p| p.window_first_slot).collect();
        ws.sort();
        assert!(ws.windows(2).all(|w| w[1] - w[0] >= 8));
        assert!(ws.iter().all(|&w| w >= 100));
        for t in ["a", "b"] {
            let mine: Vec<_> = plan.iter().filter(|p| p.target == t).collect();
            assert_eq!(mine.len(), 11);
            let rpc = mine.iter().filter(|p| p.path == Path::Rpc).count();
            let sweep = mine.iter().filter(|p| p.design.kind == ProbeKind::Sweep).count();
            assert_eq!((rpc, sweep), (1, 2));
        }
        assert!(plan.iter().enumerate().all(|(i, p)| p.batch_id as usize == i));
    }

    #[test]
    fn deterministic() {
        let a = target("a", (0..30).map(|i| 104 + i * 12).collect());
        assert_eq!(plan_hash(&plan(&[a.clone()], &params())), plan_hash(&plan(&[a], &params())));
    }
}

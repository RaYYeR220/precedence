//! Experimental design.
//!
//! Every batch is one hypothesis test aimed at one leader window. What the
//! batch varies (fee, send order, send spacing, payer conflict, wire format)
//! is drawn from a seeded RNG and written to `design.json` before the first
//! transaction leaves the machine, so the design is fixed before any outcome
//! is seen.

use crate::tx::TxFormat;
use rand::{seq::SliceRandom, RngExt, SeedableRng};
use rand_chacha::ChaCha20Rng;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Priority fees (lamports) for the 8-probe ladder. Spans our probes'
/// reward/cost from below to above the typical post-Alpenglow block.
pub const LADDER8: [u64; 8] = [0, 10, 40, 150, 500, 1_500, 4_000, 10_000];
/// 12-step ladder for the time sweep (strictly increasing).
pub const LADDER12: [u64; 12] = [0, 5, 15, 40, 100, 250, 500, 1_000, 2_000, 4_000, 7_000, 10_000];
/// Fee shared by every probe of a tie batch.
pub const TIE_FEE: u64 = 500;
/// Fee levels of the format batch; each level is sent once as v1, once as legacy.
pub const FORMAT_LEVELS: [u64; 4] = [0, 150, 1_500, 10_000];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProbeKind {
    /// Burst of 8, fee ladder ⟂ send order. Does fee decide among
    /// transactions that are queued together?
    Ladder,
    /// 8 equal-fee probes sent at a fixed spacing. With priority tied, does
    /// the leader keep arrival order (FIFO), reverse it (LIFO), or neither?
    Tie,
    /// 12 probes spread over ~120–240 ms with fee falling as send time
    /// rises. Continuous FIFO keeps send order, pure priority reverses it,
    /// a batch auction of length w reverses it only inside each w window.
    Sweep,
    /// 4 v1 + 4 legacy at equal reward/cost. Does the wire format matter?
    Format,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PayerArm {
    /// Every probe has its own fee payer and writes nothing else: no conflicts.
    Distinct,
    /// Every probe also write-locks one shared account: the leader must
    /// serialize them, so parallel execution cannot reorder them.
    Shared,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Anchor {
    /// First send this many slots before the window's first slot.
    BeforeWindow { slots: f32 },
    /// First send at this many slots after the window's first slot.
    InWindow { slots: f32 },
}

impl Anchor {
    /// Offset of the first send relative to the window start, in µs.
    pub fn offset_us(&self, slot_us: f64) -> i64 {
        match *self {
            Anchor::BeforeWindow { slots } => -(slots as f64 * slot_us).round() as i64,
            Anchor::InWindow { slots } => (slots as f64 * slot_us).round() as i64,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Probe {
    /// Index inside the batch; also the memo tag's last field.
    pub idx: u16,
    pub priority_fee: u64,
    pub format: TxFormat,
    /// 0-based position in the send sequence.
    pub send_rank: u16,
    /// Planned send time relative to the batch's first send, µs.
    pub send_offset_us: i64,
    /// Which pooled payer pays this probe (index into the run's payer list).
    pub payer_slot: u16,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BatchDesign {
    pub kind: ProbeKind,
    pub arm: PayerArm,
    pub anchor: Anchor,
    /// Spacing between consecutive sends, µs (0 = burst).
    pub spacing_us: i64,
    pub seed: u64,
    pub probes: Vec<Probe>,
}

pub fn batch_seed(run_seed: u64, batch: u64) -> u64 {
    let mut h = Sha256::new();
    h.update(run_seed.to_le_bytes());
    h.update(batch.to_le_bytes());
    u64::from_le_bytes(h.finalize()[..8].try_into().unwrap())
}

/// Draw one batch. `payer_pool` is how many pooled payers exist; a batch
/// never uses the same payer twice.
pub fn design_batch(kind: ProbeKind, arm: PayerArm, anchor: Anchor, seed: u64, payer_pool: u16) -> BatchDesign {
    let mut rng = ChaCha20Rng::seed_from_u64(seed);
    let (fees, formats, spacing_us): (Vec<u64>, Vec<TxFormat>, i64) = match kind {
        ProbeKind::Ladder => {
            let mut f = LADDER8.to_vec();
            f.shuffle(&mut rng);
            (f, vec![TxFormat::V1; 8], 0)
        }
        ProbeKind::Tie => {
            let spacing = *[500i64, 2_000, 5_000].choose_ref(&mut rng);
            (vec![TIE_FEE; 8], vec![TxFormat::V1; 8], spacing)
        }
        ProbeKind::Sweep => {
            let spacing = *[10_000i64, 20_000].choose_ref(&mut rng);
            // Assigned below in send order: highest fee first, lowest last.
            (LADDER12.to_vec(), vec![TxFormat::V1; 12], spacing)
        }
        ProbeKind::Format => {
            let mut pairs: Vec<(u64, TxFormat)> = FORMAT_LEVELS
                .iter()
                .flat_map(|&f| [(f, TxFormat::V1), (f, TxFormat::Legacy)])
                .collect();
            pairs.shuffle(&mut rng);
            (pairs.iter().map(|p| p.0).collect(), pairs.iter().map(|p| p.1).collect(), 0)
        }
    };
    let n = fees.len();
    // Send order independent of fee: a fresh permutation (except the sweep,
    // whose whole point is fee anti-correlated with send time).
    let mut send_rank: Vec<u16> = (0..n as u16).collect();
    let mut fees = fees;
    if kind == ProbeKind::Sweep {
        fees.sort_unstable_by(|a, b| b.cmp(a)); // idx 0 = highest fee = sent first
    } else {
        send_rank.shuffle(&mut rng);
    }
    let mut payers: Vec<u16> = (0..payer_pool).collect();
    payers.shuffle(&mut rng);
    assert!(payer_pool as usize >= n, "payer pool smaller than batch");
    let probes = (0..n)
        .map(|i| Probe {
            idx: i as u16,
            priority_fee: fees[i],
            format: formats[i],
            send_rank: send_rank[i],
            send_offset_us: send_rank[i] as i64 * spacing_us,
            payer_slot: payers[i],
        })
        .collect();
    BatchDesign { kind, arm, anchor, spacing_us, seed, probes }
}

trait ChooseRef<T> {
    fn choose_ref<R: rand::Rng>(&self, rng: &mut R) -> &T;
}
impl<T> ChooseRef<T> for [T] {
    fn choose_ref<R: rand::Rng>(&self, rng: &mut R) -> &T {
        &self[rng.random_range(0..self.len())]
    }
}

pub fn design_hash(d: &BatchDesign) -> String {
    hex::encode(Sha256::digest(serde_json::to_vec(d).unwrap()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kendall(a: &[f64], b: &[f64]) -> f64 {
        let n = a.len();
        let mut s = 0i64;
        for i in 0..n {
            for j in i + 1..n {
                let x = (a[i] - a[j]).signum() * (b[i] - b[j]).signum();
                s += x as i64;
            }
        }
        s as f64 / (n * (n - 1) / 2) as f64
    }

    const A: Anchor = Anchor::BeforeWindow { slots: 1.0 };

    #[test]
    fn deterministic_and_hash_stable() {
        let a = design_batch(ProbeKind::Ladder, PayerArm::Distinct, A, 42, 16);
        let b = design_batch(ProbeKind::Ladder, PayerArm::Distinct, A, 42, 16);
        assert_eq!(a, b);
        assert_eq!(design_hash(&a), design_hash(&b));
        let c = design_batch(ProbeKind::Ladder, PayerArm::Distinct, A, 43, 16);
        assert_ne!(design_hash(&a), design_hash(&c));
    }

    #[test]
    fn ladder_fee_is_independent_of_send_order() {
        let mut taus = Vec::new();
        for seed in 0..40_000u64 {
            let d = design_batch(ProbeKind::Ladder, PayerArm::Distinct, A, batch_seed(7, seed), 16);
            let f: Vec<f64> = d.probes.iter().map(|p| p.priority_fee as f64).collect();
            let r: Vec<f64> = d.probes.iter().map(|p| p.send_rank as f64).collect();
            taus.push(kendall(&f, &r));
            let mut fees: Vec<u64> = d.probes.iter().map(|p| p.priority_fee).collect();
            fees.sort();
            assert_eq!(fees, LADDER8.to_vec());
        }
        let mean = taus.iter().sum::<f64>() / taus.len() as f64;
        // SE of the mean of tau for n=8 under independence is ~0.289/sqrt(N).
        let se = 0.289 / (taus.len() as f64).sqrt();
        assert!(mean.abs() < 4.0 * se, "mean tau {mean}, se {se}");
    }

    #[test]
    fn sweep_fee_strictly_decreasing_in_send_time() {
        for seed in 0..200 {
            let d = design_batch(ProbeKind::Sweep, PayerArm::Distinct, A, seed, 16);
            let mut by_time = d.probes.clone();
            by_time.sort_by_key(|p| p.send_offset_us);
            for w in by_time.windows(2) {
                assert!(w[0].priority_fee > w[1].priority_fee);
                assert!(w[1].send_offset_us - w[0].send_offset_us == d.spacing_us);
            }
            assert!(d.spacing_us == 10_000 || d.spacing_us == 20_000);
        }
    }

    #[test]
    fn tie_equal_fees_strictly_spaced() {
        for seed in 0..200 {
            let d = design_batch(ProbeKind::Tie, PayerArm::Shared, A, seed, 16);
            assert!(d.probes.iter().all(|p| p.priority_fee == TIE_FEE));
            let mut offs: Vec<i64> = d.probes.iter().map(|p| p.send_offset_us).collect();
            offs.sort();
            assert!(offs.windows(2).all(|w| w[1] > w[0]));
        }
    }

    #[test]
    fn format_pairs_balanced_and_payers_unique() {
        for seed in 0..200 {
            let d = design_batch(ProbeKind::Format, PayerArm::Distinct, A, seed, 16);
            for lvl in FORMAT_LEVELS {
                let at: Vec<_> = d.probes.iter().filter(|p| p.priority_fee == lvl).collect();
                assert_eq!(at.len(), 2);
                assert_ne!(at[0].format, at[1].format);
            }
            let mut payers: Vec<u16> = d.probes.iter().map(|p| p.payer_slot).collect();
            payers.sort();
            payers.dedup();
            assert_eq!(payers.len(), d.probes.len());
        }
    }

    #[test]
    fn anchor_offsets() {
        assert_eq!(Anchor::BeforeWindow { slots: 1.0 }.offset_us(213_000.0), -213_000);
        assert_eq!(Anchor::InWindow { slots: 0.5 }.offset_us(213_000.0), 106_500);
    }
}

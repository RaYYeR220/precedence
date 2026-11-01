//! Which validators to aim at, and when.

use crate::snapshot::{Snapshot, SLOTS_PER_WINDOW};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Target {
    pub identity: String,
    pub family: String,
    pub version: String,
    pub stake: u64,
    pub tpu_quic: String,
    pub tpu_is_proxy: bool,
    /// First slots of this validator's upcoming 4-slot windows.
    pub windows: Vec<u64>,
}

/// First slot of every window a leader owns in `[from, to)`.
pub fn windows_of(slots: &[u64], from: u64, to: u64) -> Vec<u64> {
    let mut w: Vec<u64> = slots
        .iter()
        .filter(|&&s| s >= from && s < to)
        .map(|s| s / SLOTS_PER_WINDOW * SLOTS_PER_WINDOW)
        .collect();
    w.dedup();
    // Only windows the validator owns entirely (all four slots in the schedule).
    w.retain(|&first| (0..SLOTS_PER_WINDOW).all(|i| slots.binary_search(&(first + i)).is_ok()));
    w
}

#[derive(Debug, Clone)]
pub struct Selection {
    /// Families to cover, in priority order.
    pub families: Vec<String>,
    pub per_family: usize,
    pub min_stake_lamports: u64,
    pub min_windows: usize,
    pub from_slot: u64,
    pub to_slot: u64,
}

/// For each family: the highest-stake validators running the family's most
/// staked version, with a reachable `tpuQuic` and at least `min_windows`
/// whole windows in range.
pub fn pick_targets(snap: &Snapshot, sel: &Selection) -> Vec<Target> {
    let mut out = Vec::new();
    for family in &sel.families {
        let members: Vec<_> = snap.nodes.iter().filter(|n| &n.client_id == family && n.tpu_quic.is_some()).collect();
        let mut version_stake: HashMap<&str, u64> = HashMap::new();
        for n in &members {
            *version_stake.entry(n.version.as_str()).or_default() += snap.stake.get(&n.pubkey).copied().unwrap_or(0);
        }
        let Some((dominant, _)) = version_stake.iter().max_by_key(|(v, s)| (**s, std::cmp::Reverse(**v))) else {
            continue;
        };
        let mut cands: Vec<Target> = members
            .iter()
            .filter(|n| n.version == *dominant)
            .filter_map(|n| {
                let stake = snap.stake.get(&n.pubkey).copied().unwrap_or(0);
                if stake < sel.min_stake_lamports {
                    return None;
                }
                let slots = snap.leader_slots.get(&n.pubkey)?;
                let windows = windows_of(slots, sel.from_slot, sel.to_slot);
                (windows.len() >= sel.min_windows).then(|| Target {
                    identity: n.pubkey.clone(),
                    family: family.clone(),
                    version: n.version.clone(),
                    stake,
                    tpu_quic: n.tpu_quic.clone().unwrap(),
                    tpu_is_proxy: n.tpu_is_proxy(),
                    windows,
                })
            })
            .collect();
        cands.sort_by(|a, b| b.stake.cmp(&a.stake).then(a.identity.cmp(&b.identity)));
        out.extend(cands.into_iter().take(sel.per_family));
    }
    out
}

/// Stake share per family, for the report header.
pub fn family_stake(snap: &Snapshot) -> BTreeMap<String, u64> {
    let mut m = BTreeMap::new();
    for n in &snap.nodes {
        *m.entry(n.client_id.clone()).or_default() += snap.stake.get(&n.pubkey).copied().unwrap_or(0);
    }
    m
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::snapshot::{FeatureStatus, Node};

    fn node(pk: &str, fam: &str, ver: &str) -> Node {
        Node {
            pubkey: pk.into(),
            client_id: fam.into(),
            version: ver.into(),
            gossip: Some("1.1.1.1:8001".into()),
            tpu_quic: Some("1.1.1.1:8009".into()),
        }
    }

    #[test]
    fn windows_need_all_four_slots() {
        let slots = vec![100, 101, 102, 103, 108, 109, 110];
        assert_eq!(windows_of(&slots, 0, 1_000), vec![100]);
        assert_eq!(windows_of(&slots, 101, 1_000), vec![100]);
        assert_eq!(windows_of(&slots, 104, 1_000), Vec::<u64>::new());
    }

    #[test]
    fn picks_dominant_version_by_stake() {
        let snap = Snapshot {
            taken_unix_us: 0,
            slot: 0,
            epoch: 0,
            epoch_start_slot: 0,
            slots_in_epoch: 1_000,
            nodes: vec![node("a", "F", "1.0"), node("b", "F", "2.0"), node("c", "F", "2.0"), node("d", "G", "1.0")],
            stake: [("a", 100u64), ("b", 60), ("c", 70), ("d", 5)].iter().map(|(k, v)| (k.to_string(), *v)).collect(),
            leader_slots: ["a", "b", "c", "d"]
                .iter()
                .enumerate()
                .map(|(i, k)| (k.to_string(), (0..4).map(|j| 400 + i as u64 * 4 + j).collect()))
                .collect(),
            features: FeatureStatus { alpenglow_activated_slot: Some(1), fast_leader_handover_activated_slot: None },
        };
        let sel = Selection {
            families: vec!["F".into(), "G".into()],
            per_family: 1,
            min_stake_lamports: 10,
            min_windows: 1,
            from_slot: 0,
            to_slot: 1_000,
        };
        let t = pick_targets(&snap, &sel);
        // version 2.0 has 130 stake > 1.0's 100, so "c" (70) wins over "a".
        assert_eq!(t.len(), 1);
        assert_eq!(t[0].identity, "c");
    }
}

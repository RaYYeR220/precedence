//! Independent re-check of the stored evidence against a public RPC node:
//! for every block that holds a probe, the probe's signature must sit at the
//! recorded index, the block's `Fee` reward must name the recorded leader,
//! and the stored signature list must equal the live one. Entry boundaries,
//! where recorded, must partition the block's transactions exactly.

use crate::collect::TrimmedBlock;
use crate::io::{read_json_gz, read_jsonl, sha256_hex};
use crate::rpc::Rpc;
use anyhow::Result;
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

#[derive(Debug, Serialize, Default)]
pub struct VerifyReport {
    pub rpc: String,
    pub blocks_checked: usize,
    pub probes_checked: usize,
    pub mismatches: Vec<String>,
    pub entry_slots_checked: usize,
    pub entry_mismatches: Vec<String>,
}

/// Signatures of a block in ledger order, as `getBlock` returns them.
pub fn live_signatures(v: &Value) -> Vec<String> {
    v["signatures"].as_array().map(|a| a.iter().filter_map(|s| s.as_str().map(str::to_string)).collect()).unwrap_or_default()
}

pub fn leader_of(v: &Value) -> Option<String> {
    v["rewards"].as_array()?.iter().find(|r| r["rewardType"] == "Fee").and_then(|r| r["pubkey"].as_str().map(str::to_string))
}

/// Compare one stored block to its live counterpart.
pub fn compare_block(stored: &TrimmedBlock, live: &Value, probe_sigs: &[String]) -> Vec<String> {
    let mut errs = Vec::new();
    let live_sigs = live_signatures(live);
    let stored_sigs: Vec<String> = stored.txs.iter().map(|t| t.sig.clone()).collect();
    if sha256_hex(stored_sigs.join(",").as_bytes()) != sha256_hex(live_sigs.join(",").as_bytes()) {
        errs.push(format!("slot {}: stored transaction order differs from getBlock", stored.slot));
    }
    if leader_of(live) != stored.leader {
        errs.push(format!("slot {}: leader {:?} != stored {:?}", stored.slot, leader_of(live), stored.leader));
    }
    for sig in probe_sigs {
        let at_stored = stored.txs.iter().position(|t| &t.sig == sig);
        let at_live = live_sigs.iter().position(|s| s == sig);
        if at_stored.is_none() || at_stored != at_live {
            errs.push(format!("slot {}: probe {sig} at {at_stored:?} stored vs {at_live:?} live", stored.slot));
        }
    }
    errs
}

/// Entries with transactions must tile `0..n_tx` without gaps or overlaps.
pub fn entries_cover(n_tx: usize, entries: &[(u64, u64)]) -> Result<(), String> {
    let mut e: Vec<(u64, u64)> = entries.iter().copied().filter(|(_, c)| *c > 0).collect();
    e.sort();
    let mut next = 0u64;
    for (start, count) in e {
        if start != next {
            return Err(format!("entry starts at {start}, expected {next}"));
        }
        next = start + count;
    }
    if next as usize != n_tx {
        return Err(format!("entries cover {next} of {n_tx} transactions"));
    }
    Ok(())
}

pub async fn verify(rpc: &Rpc, data_dir: &Path) -> Result<VerifyReport> {
    let mut report = VerifyReport { rpc: rpc.url().to_string(), ..Default::default() };
    // probe signature -> slot, over all runs
    let mut by_slot: BTreeMap<u64, Vec<String>> = BTreeMap::new();
    let mut entries_by_slot: HashMap<u64, Vec<(u64, u64)>> = HashMap::new();
    let runs = data_dir.join("runs");
    if runs.exists() {
        for run in std::fs::read_dir(&runs)? {
            let run = run?.path();
            let st = run.join("statuses.json");
            if st.exists() {
                let v: Value = serde_json::from_slice(&std::fs::read(st)?)?;
                for (sig, s) in v.as_object().into_iter().flatten() {
                    if let Some(slot) = s["slot"].as_u64() {
                        by_slot.entry(slot).or_default().push(sig.clone());
                    }
                }
            }
            let en = run.join("entries.jsonl");
            if en.exists() {
                for e in read_jsonl(en)? {
                    if let (Some(slot), Some(start), Some(count)) =
                        (e["slot"].as_u64(), e["starting_transaction_index"].as_u64(), e["executed_transaction_count"].as_u64())
                    {
                        entries_by_slot.entry(slot).or_default().push((start, count));
                    }
                }
            }
        }
    }
    let slots: BTreeSet<u64> = by_slot.keys().copied().collect();
    for slot in slots {
        let path = crate::collect::block_path(data_dir, slot);
        if !path.exists() {
            report.mismatches.push(format!("slot {slot}: block file missing"));
            continue;
        }
        let stored: TrimmedBlock = serde_json::from_value(read_json_gz(&path)?)?;
        let Some(live) = rpc.block_signatures(slot).await? else {
            report.mismatches.push(format!("slot {slot}: getBlock returned no block"));
            continue;
        };
        let sigs = &by_slot[&slot];
        report.mismatches.extend(compare_block(&stored, &live, sigs));
        report.blocks_checked += 1;
        report.probes_checked += sigs.len();
        if let Some(e) = entries_by_slot.get(&slot) {
            report.entry_slots_checked += 1;
            if let Err(why) = entries_cover(stored.txs.len(), e) {
                report.entry_mismatches.push(format!("slot {slot}: {why}"));
            }
        }
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collect::TrimmedTx;
    use serde_json::json;

    fn tx(i: u32, sig: &str) -> TrimmedTx {
        TrimmedTx {
            i,
            sig: sig.into(),
            version: "1".into(),
            fee: 5000,
            cost_units: None,
            cu_consumed: None,
            priority_fee: 0,
            cu_limit: None,
            n_sigs: 1,
            payer: "p".into(),
            writable: vec![],
            err: false,
        }
    }

    fn stored() -> TrimmedBlock {
        TrimmedBlock {
            slot: 5,
            blockhash: "b".into(),
            previous_blockhash: "a".into(),
            parent_slot: 4,
            block_time: None,
            block_height: None,
            leader: Some("L".into()),
            raw_sha256: "x".into(),
            txs: vec![tx(0, "s0"), tx(1, "s1"), tx(2, "s2")],
        }
    }

    #[test]
    fn matching_block_passes() {
        let live = json!({"signatures": ["s0", "s1", "s2"], "rewards": [{"pubkey": "L", "rewardType": "Fee"}]});
        assert!(compare_block(&stored(), &live, &["s1".into()]).is_empty());
    }

    #[test]
    fn swapped_probe_is_reported() {
        let live = json!({"signatures": ["s0", "s2", "s1"], "rewards": [{"pubkey": "L", "rewardType": "Fee"}]});
        let errs = compare_block(&stored(), &live, &["s1".into()]);
        assert!(errs.iter().any(|e| e.contains("probe s1")));
        assert!(errs.iter().any(|e| e.contains("order differs")));
    }

    #[test]
    fn wrong_leader_is_reported() {
        let live = json!({"signatures": ["s0", "s1", "s2"], "rewards": [{"pubkey": "M", "rewardType": "Fee"}]});
        assert!(compare_block(&stored(), &live, &[]).iter().any(|e| e.contains("leader")));
    }

    #[test]
    fn entry_partition() {
        assert!(entries_cover(5, &[(0, 2), (2, 0), (2, 3)]).is_ok());
        assert!(entries_cover(5, &[(0, 2), (3, 2)]).is_err());
        assert!(entries_cover(5, &[(0, 2), (2, 2)]).is_err());
    }
}

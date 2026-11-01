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

/// One entry as recorded: where its transactions start, how many, how many
/// hashes it carries, and its hash.
#[derive(Debug, Clone, PartialEq)]
pub struct EntryRec {
    pub index: u64,
    pub start: u64,
    pub count: u64,
    pub num_hashes: u64,
    pub hash: String,
}

fn sha(parts: &[&[u8]]) -> [u8; 32] {
    use sha2::Digest;
    let mut h = sha2::Sha256::new();
    for p in parts {
        h.update(p);
    }
    h.finalize().into()
}

/// Merkle root over signatures, as the ledger commits to them (leaf prefix
/// 0x00, node prefix 0x01, an odd node is paired with itself).
pub fn signature_merkle_root(sigs: &[Vec<u8>]) -> [u8; 32] {
    if sigs.is_empty() {
        return [0u8; 32];
    }
    let mut level: Vec<[u8; 32]> = sigs.iter().map(|s| sha(&[&[0u8], s])).collect();
    while level.len() > 1 {
        level = level.chunks(2).map(|c| sha(&[&[1u8], &c[0], c.get(1).unwrap_or(&c[0])])).collect();
    }
    level[0]
}

/// Recompute the hash chain of a block's entries from its parent's
/// blockhash and its own signatures. Returns the index of the first entry
/// whose recorded hash differs, or `Ok(last_hash)`.
pub fn recompute_chain(previous_blockhash: &str, block: &TrimmedBlock, entries: &[EntryRec]) -> Result<String, u64> {
    let mut h: [u8; 32] = match bs58::decode(previous_blockhash).into_vec() {
        Ok(v) if v.len() == 32 => v.try_into().unwrap(),
        _ => return Err(0),
    };
    let mut sigs: Vec<Vec<Vec<u8>>> = Vec::with_capacity(block.txs.len());
    for t in &block.txs {
        let mut v = vec![bs58::decode(&t.sig).into_vec().unwrap_or_default()];
        v.extend(t.more_sigs.iter().map(|s| bs58::decode(s).into_vec().unwrap_or_default()));
        sigs.push(v);
    }
    let mut es = entries.to_vec();
    es.sort_by_key(|e| e.index);
    for e in &es {
        if e.count == 0 {
            for _ in 0..e.num_hashes {
                h = sha(&[&h]);
            }
        } else {
            for _ in 0..e.num_hashes.saturating_sub(1) {
                h = sha(&[&h]);
            }
            let (s, c) = (e.start as usize, e.count as usize);
            let flat: Vec<Vec<u8>> = sigs.get(s..s + c).map(|x| x.iter().flatten().cloned().collect()).unwrap_or_default();
            h = sha(&[&h, &signature_merkle_root(&flat)]);
        }
        if bs58::encode(h).into_string() != e.hash {
            return Err(e.index);
        }
    }
    Ok(bs58::encode(h).into_string())
}

#[derive(Debug, Serialize, Default)]
pub struct VerifyReport {
    pub rpc: String,
    pub blocks_checked: usize,
    pub probes_checked: usize,
    pub mismatches: Vec<String>,
    pub entry_slots_checked: usize,
    pub entry_mismatches: Vec<String>,
    /// Blocks whose entry hashes we recomputed from signatures, and of those
    /// how many chains end exactly on the block's blockhash.
    pub poh_chains_checked: usize,
    pub poh_chains_reach_blockhash: usize,
    pub poh_mismatches: Vec<String>,
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
    let mut entry_recs: HashMap<u64, Vec<EntryRec>> = HashMap::new();
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
            let gz = run.join("entries.jsonl.gz");
            let en = if gz.exists() { gz } else { run.join("entries.jsonl") };
            if en.exists() {
                for e in read_jsonl(en)? {
                    if let (Some(slot), Some(start), Some(count)) =
                        (e["slot"].as_u64(), e["starting_transaction_index"].as_u64(), e["executed_transaction_count"].as_u64())
                    {
                        entries_by_slot.entry(slot).or_default().push((start, count));
                        if let (Some(index), Some(nh), Some(hash)) = (e["index"].as_u64(), e["num_hashes"].as_u64(), e["hash"].as_str()) {
                            entry_recs.entry(slot).or_default().push(EntryRec {
                                index,
                                start,
                                count,
                                num_hashes: nh,
                                hash: hash.to_string(),
                            });
                        }
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
            if let Some(recs) = entry_recs.get(&slot) {
                report.poh_chains_checked += 1;
                match recompute_chain(&stored.previous_blockhash, &stored, recs) {
                    Ok(last) if last == stored.blockhash => report.poh_chains_reach_blockhash += 1,
                    Ok(last) => report.poh_mismatches.push(format!("slot {slot}: chain ends at {last}, blockhash {}", stored.blockhash)),
                    Err(i) => report.poh_mismatches.push(format!("slot {slot}: entry {i} hash differs from its recomputation")),
                }
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
            more_sigs: vec![],
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
    fn merkle_root_shapes() {
        let a = vec![1u8; 64];
        let b = vec![2u8; 64];
        let c = vec![3u8; 64];
        assert_eq!(signature_merkle_root(&[a.clone()]), sha(&[&[0u8], &a]));
        let la = sha(&[&[0u8], &a]);
        let lb = sha(&[&[0u8], &b]);
        let lc = sha(&[&[0u8], &c]);
        assert_eq!(signature_merkle_root(&[a.clone(), b.clone()]), sha(&[&[1u8], &la, &lb]));
        let ab = sha(&[&[1u8], &la, &lb]);
        let cc = sha(&[&[1u8], &lc, &lc]);
        assert_eq!(signature_merkle_root(&[a, b, c]), sha(&[&[1u8], &ab, &cc]));
    }

    #[test]
    fn entry_partition() {
        assert!(entries_cover(5, &[(0, 2), (2, 0), (2, 3)]).is_ok());
        assert!(entries_cover(5, &[(0, 2), (3, 2)]).is_err());
        assert!(entries_cover(5, &[(0, 2), (2, 2)]).is_err());
    }
}

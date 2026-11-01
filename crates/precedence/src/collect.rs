//! After a run: where did every probe land, and what did those blocks hold?

use crate::io::{read_jsonl, sha256_hex, write_json, write_json_gz};
use crate::rpc::Rpc;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

pub const COMPUTE_BUDGET: &str = "ComputeBudget111111111111111111111111111111";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TrimmedTx {
    pub i: u32,
    pub sig: String,
    pub version: String,
    pub fee: u64,
    pub cost_units: Option<u64>,
    pub cu_consumed: Option<u64>,
    pub priority_fee: u64,
    pub cu_limit: Option<u64>,
    pub n_sigs: u64,
    pub payer: String,
    pub writable: Vec<String>,
    pub err: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TrimmedBlock {
    pub slot: u64,
    pub blockhash: String,
    pub previous_blockhash: String,
    pub parent_slot: u64,
    pub block_time: Option<i64>,
    pub block_height: Option<u64>,
    pub leader: Option<String>,
    pub raw_sha256: String,
    pub txs: Vec<TrimmedTx>,
}

fn version_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        _ => "legacy".into(),
    }
}

/// CU limit requested by a legacy/v0 tx (SetComputeUnitLimit), if any.
fn legacy_cu_limit(msg: &Value) -> Option<u64> {
    let keys = msg["accountKeys"].as_array()?;
    for ix in msg["instructions"].as_array()? {
        let idx = ix["programIdIndex"].as_u64()? as usize;
        if keys.get(idx)?.as_str()? != COMPUTE_BUDGET {
            continue;
        }
        let data = bs58::decode(ix["data"].as_str()?).into_vec().ok()?;
        if data.first() == Some(&2) && data.len() >= 5 {
            return Some(u32::from_le_bytes(data[1..5].try_into().ok()?) as u64);
        }
    }
    None
}

fn writable_accounts(msg: &Value, meta: &Value) -> Vec<String> {
    let keys: Vec<&str> = msg["accountKeys"].as_array().map(|a| a.iter().filter_map(|k| k.as_str()).collect()).unwrap_or_default();
    let h = &msg["header"];
    let n = keys.len();
    let req = h["numRequiredSignatures"].as_u64().unwrap_or(0) as usize;
    let ro_signed = h["numReadonlySignedAccounts"].as_u64().unwrap_or(0) as usize;
    let ro_unsigned = h["numReadonlyUnsignedAccounts"].as_u64().unwrap_or(0) as usize;
    let mut out: Vec<String> = keys
        .iter()
        .enumerate()
        .filter(|(i, _)| if *i < req { *i < req.saturating_sub(ro_signed) } else { *i < n.saturating_sub(ro_unsigned) })
        .map(|(_, k)| k.to_string())
        .collect();
    if let Some(w) = meta["loadedAddresses"]["writable"].as_array() {
        out.extend(w.iter().filter_map(|k| k.as_str().map(str::to_string)));
    }
    out
}

pub fn trim_block(slot: u64, raw: &Value) -> TrimmedBlock {
    let raw_sha256 = sha256_hex(&serde_json::to_vec(raw).unwrap());
    let leader = raw["rewards"]
        .as_array()
        .and_then(|r| r.iter().find(|x| x["rewardType"].as_str() == Some("Fee")))
        .and_then(|x| x["pubkey"].as_str().map(str::to_string));
    let txs = raw["transactions"]
        .as_array()
        .map(|a| {
            a.iter()
                .enumerate()
                .map(|(i, t)| {
                    let msg = &t["transaction"]["message"];
                    let meta = &t["meta"];
                    let sigs = t["transaction"]["signatures"].as_array().cloned().unwrap_or_default();
                    let n_sigs = sigs.len() as u64;
                    let fee = meta["fee"].as_u64().unwrap_or(0);
                    let version = version_str(&t["version"]);
                    let cu_limit = if version == "1" {
                        msg["transactionConfig"]["computeUnitLimit"].as_u64()
                    } else {
                        legacy_cu_limit(msg)
                    };
                    TrimmedTx {
                        i: i as u32,
                        sig: sigs.first().and_then(|s| s.as_str()).unwrap_or("").to_string(),
                        version,
                        fee,
                        cost_units: meta["costUnits"].as_u64(),
                        cu_consumed: meta["computeUnitsConsumed"].as_u64(),
                        // fee = 5000 per signature + priority fee (SIMD-0096 / SIMD-0385)
                        priority_fee: fee.saturating_sub(5_000 * n_sigs),
                        cu_limit,
                        n_sigs,
                        payer: msg["accountKeys"][0].as_str().unwrap_or("").to_string(),
                        writable: writable_accounts(msg, meta),
                        err: !meta["err"].is_null(),
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    TrimmedBlock {
        slot,
        blockhash: raw["blockhash"].as_str().unwrap_or("").to_string(),
        previous_blockhash: raw["previousBlockhash"].as_str().unwrap_or("").to_string(),
        parent_slot: raw["parentSlot"].as_u64().unwrap_or(0),
        block_time: raw["blockTime"].as_i64(),
        block_height: raw["blockHeight"].as_u64(),
        leader,
        raw_sha256,
        txs,
    }
}

pub fn block_path(data_dir: &Path, slot: u64) -> std::path::PathBuf {
    data_dir.join("blocks").join(format!("{slot}.json.gz"))
}

/// Statuses for every probe, then every block that holds one.
pub async fn collect(rpc: &Rpc, data_dir: &Path, run_dir: &Path) -> Result<CollectSummary> {
    let probes = read_jsonl(run_dir.join("probes.jsonl")).context("probes.jsonl")?;
    let sigs: Vec<String> = probes
        .iter()
        .filter(|p| p["sent_unix_us"].is_u64() && p["send_err"].is_null())
        .filter_map(|p| p["signature"].as_str().map(str::to_string))
        .collect();
    let statuses = rpc.signature_statuses(&sigs).await?;
    let mut map: BTreeMap<String, Value> = BTreeMap::new();
    let mut slots = BTreeSet::new();
    for (sig, st) in sigs.iter().zip(statuses) {
        if st.is_null() {
            map.insert(sig.clone(), Value::Null);
        } else {
            let slot = st["slot"].as_u64();
            if let Some(s) = slot {
                slots.insert(s);
            }
            map.insert(sig.clone(), json!({"slot": slot, "err": st["err"]}));
        }
    }
    write_json(run_dir.join("statuses.json"), &map)?;
    let mut fetched = 0;
    let mut missing = Vec::new();
    for &slot in &slots {
        let path = block_path(data_dir, slot);
        if path.exists() {
            continue;
        }
        match rpc.block(slot).await? {
            Some(raw) => {
                write_json_gz(&path, &trim_block(slot, &raw))?;
                fetched += 1;
            }
            None => missing.push(slot),
        }
    }
    let landed = map.values().filter(|v| !v.is_null()).count();
    tracing::info!(probes = sigs.len(), landed, blocks = slots.len(), fetched, "collected");
    Ok(CollectSummary { probes_sent: sigs.len(), landed, blocks: slots.len(), fetched, missing })
}

#[derive(Debug, Serialize)]
pub struct CollectSummary {
    pub probes_sent: usize,
    pub landed: usize,
    pub blocks: usize,
    pub fetched: usize,
    pub missing: Vec<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn raw_block() -> Value {
        json!({
            "blockhash": "BH", "previousBlockhash": "PBH", "parentSlot": 9, "blockTime": 1, "blockHeight": 2,
            "rewards": [{"pubkey": "LEADER", "rewardType": "Fee", "lamports": 1}],
            "transactions": [
                {"version": 1, "transaction": {"signatures": ["S0"], "message": {
                    "accountKeys": ["P0", "W0", "PROG"], "header": {"numRequiredSignatures": 1, "numReadonlySignedAccounts": 0, "numReadonlyUnsignedAccounts": 1},
                    "instructions": [], "transactionConfig": {"computeUnitLimit": 7000, "priorityFee": 150}}},
                 "meta": {"fee": 5150, "costUnits": 8000, "computeUnitsConsumed": 6000, "err": null, "loadedAddresses": {"writable": [], "readonly": []}}},
                {"version": "legacy", "transaction": {"signatures": ["S1", "S1b"], "message": {
                    "accountKeys": ["P1", "P1b", COMPUTE_BUDGET], "header": {"numRequiredSignatures": 2, "numReadonlySignedAccounts": 1, "numReadonlyUnsignedAccounts": 1},
                    "instructions": [{"programIdIndex": 2, "accounts": [], "data": bs58::encode([2u8, 0x40, 0x0d, 0x03, 0x00]).into_string()}]}},
                 "meta": {"fee": 10000, "costUnits": 3000, "computeUnitsConsumed": 300, "err": {"InstructionError": [0, "Custom"]}, "loadedAddresses": {"writable": ["L"], "readonly": []}}}
            ]
        })
    }

    #[test]
    fn trims_order_leader_and_fees() {
        let b = trim_block(10, &raw_block());
        assert_eq!(b.leader.as_deref(), Some("LEADER"));
        assert_eq!(b.txs.len(), 2);
        assert_eq!(b.txs[0].sig, "S0");
        assert_eq!((b.txs[0].priority_fee, b.txs[0].cu_limit), (150, Some(7000)));
        assert_eq!(b.txs[0].writable, vec!["P0", "W0"]);
        assert_eq!(b.txs[1].version, "legacy");
        assert_eq!(b.txs[1].priority_fee, 0);
        assert_eq!(b.txs[1].cu_limit, Some(200_000));
        assert_eq!(b.txs[1].writable, vec!["P1", "L"]);
        assert!(b.txs[1].err && !b.txs[0].err);
        assert_eq!(b.raw_sha256.len(), 64);
    }
}

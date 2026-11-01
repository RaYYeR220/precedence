//! Close every reservation that was never settled: a transaction that the
//! chain knows is settled at its actual fee, one that it does not know is
//! settled at zero. Run after any interrupted or skipped batch.

use crate::io::read_jsonl;
use crate::ledger::Ledger;
use crate::rpc::Rpc;
use anyhow::Result;
use serde_json::json;
use std::collections::{BTreeMap, HashSet};
use std::path::Path;

pub async fn reconcile(rpc: &Rpc, ledger: &mut Ledger, ledger_path: &Path) -> Result<serde_json::Value> {
    let mut open: BTreeMap<String, String> = BTreeMap::new();
    let mut settled: HashSet<String> = HashSet::new();
    for e in read_jsonl(ledger_path)? {
        let Some(sig) = e["signature"].as_str() else { continue };
        match e["kind"].as_str() {
            Some("reserve") => {
                open.insert(sig.to_string(), e["purpose"].as_str().unwrap_or("probe").to_string());
            }
            Some("settle") => {
                settled.insert(sig.to_string());
            }
            _ => {}
        }
    }
    open.retain(|s, _| !settled.contains(s));
    let sigs: Vec<String> = open.keys().cloned().collect();
    let statuses = rpc.signature_statuses(&sigs).await?;
    let (mut zero, mut charged, mut lamports) = (0u64, 0u64, 0u64);
    for (sig, st) in sigs.iter().zip(statuses) {
        let purpose = &open[sig];
        if st.is_null() {
            ledger.settle(purpose, sig, 0)?;
            zero += 1;
        } else {
            let tx = rpc.call("getTransaction", json!([sig, {"maxSupportedTransactionVersion": 1, "encoding": "json"}])).await?;
            let fee = tx["meta"]["fee"].as_u64().unwrap_or(0);
            ledger.settle(purpose, sig, fee)?;
            charged += 1;
            lamports += fee;
        }
    }
    Ok(json!({"open_before": sigs.len(), "settled_zero": zero, "settled_charged": charged, "lamports_charged": lamports, "totals": ledger.totals()}))
}

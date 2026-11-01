//! Positive control on a local Agave validator, whose scheduler is the open
//! one: the same probes, the same sender, the same collector and the same
//! analysis must recover the rule that the source code says is there.
//!
//! The local validator is leader of every slot, so there are no windows to
//! aim at; batches are simply spaced so that each lands in its own block.

use crate::collect;
use crate::cost;
use crate::design::{batch_seed, design_batch, design_hash, Anchor, PayerArm, ProbeKind};
use crate::io::{write_json, JsonlWriter};
use crate::keys;
use crate::ledger::Ledger;
use crate::rpc::Rpc;
use crate::send::{self, quic::QuicSender};
use crate::tx::{self, ProbeSpec};
use anyhow::{bail, Context, Result};
use rand::{seq::SliceRandom, SeedableRng};
use serde_json::json;
use solana_hash::Hash;
use solana_keypair::Keypair;
use solana_signer::Signer;
use std::path::{Path, PathBuf};
use std::str::FromStr;
use std::time::Duration;

pub struct SelftestArgs {
    pub rpc_url: String,
    pub data_dir: PathBuf,
    pub reps: usize,
    pub seed: u64,
    pub cu_limit: u32,
    pub loaded_data_limit: u32,
}

async fn airdrop(rpc: &Rpc, to: &str, lamports: u64) -> Result<()> {
    let sig = rpc.call("requestAirdrop", json!([to, lamports])).await?;
    let sig = sig.as_str().context("airdrop signature")?.to_string();
    for _ in 0..60 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let st = rpc.signature_statuses(std::slice::from_ref(&sig)).await?;
        if st.first().map(|s| !s.is_null() && s["err"].is_null()).unwrap_or(false) {
            return Ok(());
        }
    }
    bail!("airdrop not confirmed")
}

pub async fn selftest(a: SelftestArgs) -> Result<PathBuf> {
    let tb = crate::clock::TimeBase::new();
    let rpc = Rpc::new(&a.rpc_url).with_spacing("getBlock", Duration::from_millis(20));
    let run_id = format!("localnet-{:06x}", a.seed & 0xffffff);
    let run_dir = a.data_dir.join("runs").join(&run_id);
    std::fs::create_dir_all(run_dir.join("snapshot"))?;

    let master = Keypair::new();
    airdrop(&rpc, &master.pubkey().to_string(), 100_000_000_000).await?;
    let mut ledger = Ledger::open(a.data_dir.join("ledger").join("spend.jsonl"), crate::ledger::HARD_MAX_CAP, &run_id)?;
    crate::fund::fund(&rpc, &mut ledger, &master, 16, 1_000_000_000, false).await?;
    let payers = keys::derive_payers(&master, 16);

    let nodes = rpc.call("getClusterNodes", json!([])).await?;
    write_json(run_dir.join("snapshot").join(format!("nodes-{}.json", tb.now_us())), &nodes)?;
    let node = nodes.as_array().and_then(|a| a.first()).context("no node")?;
    let identity = node["pubkey"].as_str().unwrap_or("").to_string();
    let tpu = node["tpuQuic"].as_str().context("tpuQuic")?.to_string();

    // Balanced, shuffled list of conditions.
    let mut conds = Vec::new();
    for _ in 0..a.reps {
        conds.push((ProbeKind::Ladder, PayerArm::Distinct));
        conds.push((ProbeKind::Ladder, PayerArm::Shared));
        conds.push((ProbeKind::Tie, PayerArm::Distinct));
        conds.push((ProbeKind::Tie, PayerArm::Shared));
        conds.push((ProbeKind::Sweep, PayerArm::Distinct));
    }
    conds.shuffle(&mut rand_chacha::ChaCha20Rng::seed_from_u64(a.seed));

    let probes_log = JsonlWriter::open(run_dir.join("probes.jsonl"))?;
    let windows_log = JsonlWriter::open(run_dir.join("windows.jsonl"))?;
    let mut batches = Vec::new();
    let mut sender = QuicSender::new(tb, false)?;
    sender.connect(tpu.parse()?).await?;
    for (batch_id, (kind, arm)) in conds.into_iter().enumerate() {
        let batch_id = batch_id as u32;
        let anchor = Anchor::InWindow { slots: 0.0 };
        let design = design_batch(kind, arm, anchor, batch_seed(a.seed, batch_id as u64), 16);
        let (bh, bh_slot) = rpc.latest_blockhash().await?;
        let blockhash = Hash::from_str(&bh)?;
        let sink = (arm == PayerArm::Shared).then(|| keys::shared_sink(&run_id, batch_id));
        let mut built = Vec::new();
        for p in &design.probes {
            let payer = &payers[p.payer_slot as usize];
            let spec = ProbeSpec {
                payer: payer.pubkey(),
                priority_fee: p.priority_fee,
                cu_limit: a.cu_limit,
                loaded_data_limit: a.loaded_data_limit,
                tag: tx::make_tag((a.seed & 0xffffff) as u32, batch_id, p.idx),
                format: p.format,
                shared_write: sink,
            };
            let t = tx::build_probe(&spec, blockhash.clone(), payer)?;
            built.push((p.clone(), t.signatures[0].to_string(), tx::to_wire(&t), cost::estimated_cost(&t), spec));
        }
        let items: Vec<(String, u64)> = built.iter().map(|b| (b.1.clone(), 5_000 + b.0.priority_fee)).collect();
        ledger.reserve("probe", Some(batch_id), &items)?;
        let slot_before = rpc.slot("processed").await?;
        let mut order: Vec<usize> = (0..built.len()).collect();
        order.sort_by_key(|&i| built[i].0.send_rank);
        let first = tb.now_us() + 5_000;
        let mut recs = vec![None; built.len()];
        for &i in &order {
            send::wait_until(tb.instant_at((first as i64 + built[i].0.send_offset_us) as u64)).await;
            recs[i] = Some(sender.send(&built[i].2).await);
        }
        for (i, (p, sig, wire, est, spec)) in built.iter().enumerate() {
            let r = recs[i].as_ref().unwrap();
            probes_log.write(&json!({
                "batch_id": batch_id, "idx": p.idx, "signature": sig, "payer": spec.payer.to_string(),
                "shared_write": spec.shared_write.map(|x| x.to_string()), "priority_fee": p.priority_fee,
                "format": p.format, "est_cost": est, "agave_priority": cost::agave_priority(p.priority_fee, 1, *est),
                "send_rank": p.send_rank, "planned_offset_us": p.send_offset_us, "sent_unix_us": r.t_open_us,
                "sent_done_unix_us": r.t_done_us, "send_err": r.err, "wire_len": wire.len(),
            }));
        }
        windows_log.write(&json!({
            "batch_id": batch_id, "target": identity, "family": "LocalAgave", "version": node["version"],
            "window_first_slot": slot_before, "tpu_quic": tpu, "tpu_is_proxy": false, "path": "quic",
            "kind": kind, "arm": arm, "first_send_unix_us": first, "blockhash": bh, "blockhash_slot": bh_slot, "status": "sent",
        }));
        batches.push(json!({
            "batch_id": batch_id, "target": identity, "family": "LocalAgave", "version": node["version"],
            "tpu_quic": tpu, "tpu_is_proxy": false, "window_first_slot": slot_before, "path": "quic",
            "design_hash": design_hash(&design), "design": design,
        }));
        // Let the batch land in its own block before the next one.
        tokio::time::sleep(Duration::from_millis(1_200)).await;
    }
    write_json(run_dir.join("design.json"), &json!({"run_id": run_id, "run_seed": a.seed, "batches": batches}))?;
    write_json(
        run_dir.join("config.json"),
        &json!({"run_id": run_id, "mode": "localnet", "run_seed": a.seed, "rpc_url": a.rpc_url,
                "cu_limit": a.cu_limit, "loaded_data_limit": a.loaded_data_limit, "master": master.pubkey().to_string(),
                "git_commit": crate::run::git_commit(), "started_unix_us": tb.now_us()}),
    )?;
    tokio::time::sleep(Duration::from_secs(8)).await;
    let s = collect::collect(&rpc, &a.data_dir, &run_dir).await?;
    write_json(run_dir.join("collect.json"), &s)?;
    sender.close().await;
    Ok(run_dir)
}

/// Entry boundaries for the probe slots of a localnet run, read from the
/// ledger after the validator stopped (`agave-ledger-tool slot -vv`).
pub fn parse_ledger_tool_entries(slot: u64, text: &str) -> Vec<serde_json::Value> {
    // Lines look like: "  Entry 3 - num_hashes: 1, hash: <b58>, transactions: 5, starting_transaction_index: 12"
    let mut out = Vec::new();
    for line in text.lines() {
        let l = line.trim();
        let Some(rest) = l.strip_prefix("Entry ") else { continue };
        let Some((idx, tail)) = rest.split_once(" - ") else { continue };
        let Ok(index) = idx.trim().parse::<u64>() else { continue };
        let field = |name: &str| -> Option<String> {
            tail.split(", ").find_map(|kv| kv.strip_prefix(&format!("{name}: ")).map(|v| v.trim().to_string()))
        };
        out.push(json!({
            "slot": slot,
            "index": index,
            "num_hashes": field("num_hashes").and_then(|v| v.parse::<u64>().ok()),
            "hash": field("hash"),
            "executed_transaction_count": field("transactions").and_then(|v| v.parse::<u64>().ok()),
            "starting_transaction_index": field("starting_transaction_index").and_then(|v| v.parse::<u64>().ok()),
            "source": "ledger-tool",
        }));
    }
    out
}

pub fn dump_entries(ledger_dir: &Path, run_dir: &Path, tool: &str) -> Result<usize> {
    let statuses: serde_json::Value = serde_json::from_slice(&std::fs::read(run_dir.join("statuses.json"))?)?;
    let mut slots: Vec<u64> = statuses.as_object().into_iter().flatten().filter_map(|(_, v)| v["slot"].as_u64()).collect();
    slots.sort_unstable();
    slots.dedup();
    let w = JsonlWriter::open(run_dir.join("entries.jsonl"))?;
    let mut n = 0;
    for s in slots {
        let out = std::process::Command::new(tool)
            .args(["--ledger", &ledger_dir.to_string_lossy(), "slot", &s.to_string(), "-vv"])
            .output()
            .with_context(|| format!("running {tool}"))?;
        for e in parse_ledger_tool_entries(s, &String::from_utf8_lossy(&out.stdout)) {
            w.write(&e);
            n += 1;
        }
    }
    Ok(n)
}

#[cfg(test)]
mod tests {
    #[test]
    fn parses_ledger_tool_entry_lines() {
        let text = "Slot 42\n  Entry 0 - num_hashes: 1, hash: AAA, transactions: 3, starting_transaction_index: 0\n  Entry 1 - num_hashes: 1, hash: BBB, transactions: 0, starting_transaction_index: 3\n";
        let e = super::parse_ledger_tool_entries(42, text);
        assert_eq!(e.len(), 2);
        assert_eq!(e[0]["executed_transaction_count"], 3);
        assert_eq!(e[1]["starting_transaction_index"], 3);
        assert_eq!(e[1]["hash"], "BBB");
    }
}

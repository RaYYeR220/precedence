//! Cluster snapshot: who runs what, who leads when, which features are live.
//! Raw RPC responses are written verbatim next to the parsed view.

use crate::rpc::Rpc;
use anyhow::{anyhow, Context, Result};
use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::Write;
use std::path::Path;

/// Alpenglow (SIMD-0326/0384) feature gate.
pub const ALPENGLOW_FEATURE: &str = "A1pengvuM6JEcyNuTnMqepBKhwHE3N6PmUrdATGawhJS";
/// First slot produced under Votor on mainnet (genesis certificate + 1).
pub const FIRST_VOTOR_SLOT: u64 = 459_653_000;
/// Fast leader handover changes how blocks are built; measurements must not
/// mix regimes, so a run refuses to start once it is active.
pub const FAST_LEADER_HANDOVER_FEATURE: &str = "FastLeaderHandover11111111111111111111111111";
pub const BAM_LEADER_SLOTS_URL: &str = "https://explorer.bam.dev/api/v1/leader_slots";
pub const SLOTS_PER_WINDOW: u64 = 4;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Node {
    pub pubkey: String,
    pub client_id: String,
    pub version: String,
    pub gossip: Option<String>,
    pub tpu_quic: Option<String>,
}

impl Node {
    /// The advertised TPU sits on another host than gossip: a BAM node, a
    /// Harmonic remote TPU or a relayer receives our packets first.
    pub fn tpu_is_proxy(&self) -> bool {
        fn ip(a: &Option<String>) -> Option<String> {
            a.as_ref().and_then(|s| s.rsplit_once(':').map(|(h, _)| h.to_string()))
        }
        match (ip(&self.gossip), ip(&self.tpu_quic)) {
            (Some(g), Some(t)) => g != t,
            _ => false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct FeatureStatus {
    pub alpenglow_activated_slot: Option<u64>,
    pub fast_leader_handover_activated_slot: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Snapshot {
    pub taken_unix_us: u64,
    pub slot: u64,
    pub epoch: u64,
    pub epoch_start_slot: u64,
    pub slots_in_epoch: u64,
    pub nodes: Vec<Node>,
    /// identity -> activated stake (lamports), from getVoteAccounts.
    pub stake: HashMap<String, u64>,
    /// identity -> absolute leader slots in the snapshot's epoch.
    pub leader_slots: HashMap<String, Vec<u64>>,
    pub features: FeatureStatus,
}

/// Feature account data is `Option<u64>` (bincode): tag byte, then slot.
pub fn decode_feature(account: &Value) -> Option<u64> {
    let b64 = account.get("data")?.get(0)?.as_str()?;
    let bytes = base64::engine::general_purpose::STANDARD.decode(b64).ok()?;
    match bytes.first() {
        Some(1) if bytes.len() >= 9 => Some(u64::from_le_bytes(bytes[1..9].try_into().ok()?)),
        _ => None,
    }
}

pub fn parse_nodes(v: &Value) -> Vec<Node> {
    v.as_array()
        .map(|a| {
            a.iter()
                .filter_map(|n| {
                    Some(Node {
                        pubkey: n["pubkey"].as_str()?.to_string(),
                        client_id: n["clientId"].as_str().unwrap_or("Unknown").to_string(),
                        version: n["version"].as_str().unwrap_or("").to_string(),
                        gossip: n["gossip"].as_str().map(str::to_string),
                        tpu_quic: n["tpuQuic"].as_str().map(str::to_string),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

pub fn parse_stake(v: &Value) -> HashMap<String, u64> {
    let mut m = HashMap::new();
    for key in ["current", "delinquent"] {
        for a in v[key].as_array().into_iter().flatten() {
            if let (Some(n), Some(s)) = (a["nodePubkey"].as_str(), a["activatedStake"].as_u64()) {
                *m.entry(n.to_string()).or_default() += s;
            }
        }
    }
    m
}

pub fn parse_schedule(v: &Value, epoch_start: u64) -> HashMap<String, Vec<u64>> {
    v.as_object()
        .map(|o| {
            o.iter()
                .map(|(k, slots)| {
                    let mut s: Vec<u64> = slots
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(|x| x.as_u64())
                        .map(|i| epoch_start + i)
                        .collect();
                    s.sort_unstable();
                    (k.clone(), s)
                })
                .collect()
        })
        .unwrap_or_default()
}

fn write_json(dir: &Path, name: &str, v: &Value) -> Result<()> {
    std::fs::create_dir_all(dir)?;
    std::fs::write(dir.join(name), serde_json::to_vec(v)?)?;
    Ok(())
}

fn write_json_gz(dir: &Path, name: &str, v: &Value) -> Result<()> {
    std::fs::create_dir_all(dir)?;
    let f = std::fs::File::create(dir.join(name))?;
    let mut gz = flate2::write::GzEncoder::new(f, flate2::Compression::default());
    gz.write_all(&serde_json::to_vec(v)?)?;
    gz.finish()?;
    Ok(())
}

/// Only the cluster-nodes part, for the periodic refresh during a run.
pub async fn refresh_nodes(rpc: &Rpc, dir: &Path) -> Result<(u64, Vec<Node>)> {
    let ts = crate::ledger::now_us();
    let raw = rpc.call("getClusterNodes", json!([])).await?;
    write_json(dir, &format!("nodes-{ts}.json"), &raw)?;
    Ok((ts, parse_nodes(&raw)))
}

pub async fn take_snapshot(rpc: &Rpc, dir: &Path) -> Result<Snapshot> {
    let epoch_info = rpc.call("getEpochInfo", json!([{"commitment": "processed"}])).await?;
    write_json(dir, "epoch_info.json", &epoch_info)?;
    let slot = epoch_info["absoluteSlot"].as_u64().context("absoluteSlot")?;
    let slot_index = epoch_info["slotIndex"].as_u64().context("slotIndex")?;
    let slots_in_epoch = epoch_info["slotsInEpoch"].as_u64().context("slotsInEpoch")?;
    let epoch = epoch_info["epoch"].as_u64().context("epoch")?;
    let epoch_start_slot = slot - slot_index;

    let (taken_unix_us, nodes) = refresh_nodes(rpc, dir).await?;
    let votes = rpc.call("getVoteAccounts", json!([{"commitment": "processed"}])).await?;
    write_json(dir, "vote_accounts.json", &votes)?;
    let sched = rpc.call("getLeaderSchedule", json!([null, {"commitment": "processed"}])).await?;
    write_json_gz(dir, &format!("leader_schedule-{epoch}.json.gz"), &sched)?;

    let feats = rpc.accounts(&[ALPENGLOW_FEATURE, FAST_LEADER_HANDOVER_FEATURE]).await?;
    let features = FeatureStatus {
        alpenglow_activated_slot: feats.first().and_then(decode_feature),
        fast_leader_handover_activated_slot: feats.get(1).and_then(decode_feature),
    };
    write_json(dir, "features.json", &json!({"raw": feats, "parsed": features}))?;

    // Best effort: which leaders BAM itself lists. Not required for the run.
    match reqwest::Client::new().get(BAM_LEADER_SLOTS_URL).timeout(std::time::Duration::from_secs(20)).send().await {
        Ok(r) if r.status().is_success() => match r.json::<Value>().await {
            Ok(v) => write_json(dir, "bam_leader_slots.json", &v)?,
            Err(e) => tracing::warn!(error = %e, "bam leader slots: bad json"),
        },
        Ok(r) => tracing::warn!(status = r.status().as_u16(), "bam leader slots unavailable"),
        Err(e) => tracing::warn!(error = %e, "bam leader slots unavailable"),
    }

    Ok(Snapshot {
        taken_unix_us,
        slot,
        epoch,
        epoch_start_slot,
        slots_in_epoch,
        nodes,
        stake: parse_stake(&votes),
        leader_slots: parse_schedule(&sched, epoch_start_slot),
        features,
    })
}

/// Refuse to measure in a regime other than "Alpenglow active, fast leader
/// handover not active".
pub fn check_regime(f: &FeatureStatus) -> Result<()> {
    match f.alpenglow_activated_slot {
        Some(_) => {}
        None => return Err(anyhow!("Alpenglow feature is not active: this harness measures the post-Alpenglow regime only")),
    }
    if let Some(s) = f.fast_leader_handover_activated_slot {
        return Err(anyhow!(
            "fast leader handover is active since slot {s}: block building changed, measurements would mix regimes"
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn feature_decoding() {
        // activated at 459648000
        let mut d = vec![1u8];
        d.extend_from_slice(&459_648_000u64.to_le_bytes());
        let acc = json!({"data": [base64::engine::general_purpose::STANDARD.encode(&d), "base64"]});
        assert_eq!(decode_feature(&acc), Some(459_648_000));
        let pending = json!({"data": [base64::engine::general_purpose::STANDARD.encode([0u8; 9]), "base64"]});
        assert_eq!(decode_feature(&pending), None);
        assert_eq!(decode_feature(&Value::Null), None);
    }

    #[test]
    fn regime_gate() {
        let ok = FeatureStatus { alpenglow_activated_slot: Some(1), fast_leader_handover_activated_slot: None };
        assert!(check_regime(&ok).is_ok());
        let fh = FeatureStatus { alpenglow_activated_slot: Some(1), fast_leader_handover_activated_slot: Some(2) };
        assert!(check_regime(&fh).is_err());
        let pre = FeatureStatus { alpenglow_activated_slot: None, fast_leader_handover_activated_slot: None };
        assert!(check_regime(&pre).is_err());
    }

    #[test]
    fn feature_ids_are_valid_addresses() {
        use std::str::FromStr;
        for id in [ALPENGLOW_FEATURE, FAST_LEADER_HANDOVER_FEATURE] {
            assert!(solana_address::Address::from_str(id).is_ok(), "{id}");
        }
    }

    #[test]
    fn proxy_detection_and_parsing() {
        let v = json!([
            {"pubkey": "A", "clientId": "AgaveBam", "version": "4.3.2", "gossip": "1.2.3.4:8001", "tpuQuic": "198.13.140.227:11228"},
            {"pubkey": "B", "clientId": "Agave", "version": "4.3.2", "gossip": "5.6.7.8:8001", "tpuQuic": "5.6.7.8:8009"},
            {"pubkey": "C", "version": "4.3.2"}
        ]);
        let n = parse_nodes(&v);
        assert_eq!(n.len(), 3);
        assert!(n[0].tpu_is_proxy());
        assert!(!n[1].tpu_is_proxy());
        assert!(!n[2].tpu_is_proxy());
        assert_eq!(n[2].client_id, "Unknown");
    }

    #[test]
    fn schedule_is_absolute_and_sorted() {
        let v = json!({"X": [8, 4, 5, 6, 7, 9, 10, 11]});
        let s = parse_schedule(&v, 1_000);
        assert_eq!(s["X"], vec![1004, 1005, 1006, 1007, 1008, 1009, 1010, 1011]);
    }
}

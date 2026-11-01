//! Measure a probe's real compute and loaded-data footprint with
//! `simulateTransaction`, so the requested limits can sit just above it and
//! every probe of a run carries the same cost.

use crate::rpc::Rpc;
use crate::tx::{self, ProbeSpec, TxFormat};
use anyhow::Result;
use base64::Engine;
use serde_json::json;
use solana_hash::Hash;
use solana_keypair::Keypair;
use solana_signer::Signer;
use std::str::FromStr;

pub async fn calibrate(rpc: &Rpc, master: &Keypair) -> Result<serde_json::Value> {
    let (bh, _) = rpc.latest_blockhash().await?;
    let bh = Hash::from_str(&bh)?;
    let sink = crate::keys::shared_sink("calibrate", 0);
    let mut out = Vec::new();
    for (name, format, shared) in [
        ("v1", TxFormat::V1, None),
        ("v1_shared", TxFormat::V1, Some(sink)),
        ("legacy", TxFormat::Legacy, None),
        ("legacy_shared", TxFormat::Legacy, Some(sink)),
    ] {
        let spec = ProbeSpec {
            payer: master.pubkey(),
            priority_fee: 0,
            cu_limit: 200_000,
            loaded_data_limit: 1 << 20,
            tag: tx::make_tag(0, 0, 0),
            format,
            shared_write: shared,
        };
        let t = tx::build_probe(&spec, bh.clone(), master)?;
        let b64 = base64::engine::general_purpose::STANDARD.encode(tx::to_wire(&t));
        let sim = rpc.simulate(&b64).await;
        out.push(match sim {
            Ok(v) => json!({
                "shape": name,
                "err": v["value"]["err"],
                "units_consumed": v["value"]["unitsConsumed"],
                "loaded_accounts_data_size": v["value"]["loadedAccountsDataSize"],
                "logs": v["value"]["logs"],
                "wire_len": tx::to_wire(&t).len(),
            }),
            Err(e) => json!({"shape": name, "error": e.to_string()}),
        });
    }
    Ok(json!({"payer": master.pubkey().to_string(), "shapes": out}))
}

//! Entry recorder (Yellowstone gRPC). `getBlock` returns transactions in
//! ledger order but not the entries they were recorded in; the entry stream
//! does. For every targeted slot we keep each entry's first transaction index
//! and size, the block footer (producer time, self-declared user agent) and
//! slot-status events, each with the provider's timestamp and ours.

use crate::clock::TimeBase;
use crate::io::JsonlWriter;
use anyhow::Result;
use futures_util::StreamExt;
use serde_json::json;
use std::collections::{BTreeSet, HashMap};
use std::path::PathBuf;
use std::time::Duration;
use yellowstone_grpc_client::{ClientTlsConfig, GeyserGrpcClient};
use yellowstone_grpc_proto::prelude::{
    subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest, SubscribeRequestFilterBlockFooter,
    SubscribeRequestFilterEntry, SubscribeRequestFilterSlots,
};

fn ts_us(t: &Option<yellowstone_grpc_proto::prost_types::Timestamp>) -> Option<u64> {
    t.as_ref().map(|t| t.seconds as u64 * 1_000_000 + t.nanos as u64 / 1_000)
}

pub fn request() -> SubscribeRequest {
    SubscribeRequest {
        entry: HashMap::from([("e".to_string(), SubscribeRequestFilterEntry { include_update_parent: Some(true) })]),
        block_footer: HashMap::from([(
            "f".to_string(),
            SubscribeRequestFilterBlockFooter { include_certificates: Some(false) },
        )]),
        slots: HashMap::from([(
            "s".to_string(),
            SubscribeRequestFilterSlots { filter_by_commitment: Some(false), interslot_updates: Some(true) },
        )]),
        commitment: Some(CommitmentLevel::Processed as i32),
        ..Default::default()
    }
}

pub async fn connect(url: &str, token: Option<String>) -> Result<GeyserGrpcClient> {
    let mut b = GeyserGrpcClient::build_from_shared(url.to_string())?
        .x_token(token)?
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(30))
        .max_decoding_message_size(64 * 1024 * 1024);
    if url.starts_with("https") {
        b = b.tls_config(ClientTlsConfig::new().with_native_roots())?;
    }
    Ok(b.connect().await?)
}

/// Slots whose entries, footers and slot events are written. Filled in after
/// planning; the stream starts earlier so it can drive the slot clock.
pub type Wanted = std::sync::Arc<std::sync::Mutex<BTreeSet<u64>>>;

/// SlotStatus::SLOT_FIRST_SHRED_RECEIVED in the Yellowstone protocol.
const FIRST_SHRED_RECEIVED: i32 = 3;

/// Record until the task is aborted. Every first-shred event feeds `clock`
/// (with the provider's timestamp); only slots in `wanted` are written.
pub async fn record(
    url: String,
    token: Option<String>,
    wanted: Wanted,
    run_dir: PathBuf,
    tb: TimeBase,
    clock: Option<crate::clock::SharedClock>,
) -> Result<()> {
    let is_wanted = |s: u64| wanted.lock().unwrap().contains(&s);
    let entries = JsonlWriter::open(run_dir.join("entries.jsonl"))?;
    let footers = JsonlWriter::open(run_dir.join("footers.jsonl"))?;
    let slot_log = JsonlWriter::open(run_dir.join("slots.jsonl"))?;
    loop {
        match connect(&url, token.clone()).await {
            Ok(mut client) => match client.subscribe_with_request(Some(request())).await {
                Ok((_sink, mut stream)) => {
                    while let Some(msg) = stream.next().await {
                        let recv = tb.now_us();
                        let Ok(update) = msg else { break };
                        let created = ts_us(&update.created_at);
                        match update.update_oneof {
                            Some(UpdateOneof::Entry(e)) if is_wanted(e.slot) => entries.write(&json!({
                                "slot": e.slot,
                                "index": e.index,
                                "starting_transaction_index": e.starting_transaction_index,
                                "executed_transaction_count": e.executed_transaction_count,
                                "num_hashes": e.num_hashes,
                                "hash": bs58::encode(&e.hash).into_string(),
                                "bank_id": e.bank_id,
                                "created_at_unix_us": created,
                                "recv_unix_us": recv,
                            })),
                            Some(UpdateOneof::EntryUpdateParent(p)) if is_wanted(p.slot) => entries.write(&json!({
                                "slot": p.slot,
                                "update_parent": {"cleared_bank_id": p.cleared_bank_id, "parent_slot": p.parent_slot},
                                "created_at_unix_us": created,
                                "recv_unix_us": recv,
                            })),
                            Some(UpdateOneof::BlockFooter(f)) if is_wanted(f.slot) => footers.write(&json!({
                                "slot": f.slot,
                                "bank_id": f.bank_id,
                                "producer_time_ns": f.block_producer_time_nanos,
                                "user_agent": String::from_utf8_lossy(&f.block_user_agent),
                                "created_at_unix_us": created,
                                "recv_unix_us": recv,
                            })),
                            Some(UpdateOneof::Slot(s)) => {
                                if s.status == FIRST_SHRED_RECEIVED {
                                    if let Some(c) = &clock {
                                        c.lock().unwrap().observe(s.slot, recv, created);
                                    }
                                }
                                if is_wanted(s.slot) || s.status == FIRST_SHRED_RECEIVED {
                                    slot_log.write(&json!({
                                        "slot": s.slot,
                                        "status": s.status,
                                        "source": "grpc",
                                        "server_unix_us": created,
                                        "recv_unix_us": recv,
                                    }));
                                }
                            }
                            _ => {}
                        }
                    }
                    tracing::warn!("entry stream ended; reconnecting");
                }
                Err(e) => tracing::warn!(error = %e, "entry subscribe failed"),
            },
            Err(e) => tracing::warn!(error = %e, "entry stream connect failed"),
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

/// Stand-alone check: subscribe for a few seconds and report what arrives.
pub async fn probe(url: &str, token: Option<String>, seconds: u64) -> Result<serde_json::Value> {
    let mut client = connect(url, token).await?;
    let (_sink, mut stream) = client.subscribe_with_request(Some(request())).await?;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(seconds);
    let (mut n_entries, mut n_footers, mut n_slots) = (0u64, 0u64, 0u64);
    let mut sample_entry = None;
    let mut sample_footer = None;
    while let Ok(Some(msg)) = tokio::time::timeout_at(deadline, stream.next()).await {
        let update = msg?;
        match update.update_oneof {
            Some(UpdateOneof::Entry(e)) => {
                n_entries += 1;
                if sample_entry.is_none() && e.executed_transaction_count > 0 {
                    sample_entry = Some(json!({"slot": e.slot, "index": e.index, "num_hashes": e.num_hashes,
                        "starting_transaction_index": e.starting_transaction_index,
                        "executed_transaction_count": e.executed_transaction_count, "created_at": ts_us(&update.created_at)}));
                }
            }
            Some(UpdateOneof::BlockFooter(f)) => {
                n_footers += 1;
                if sample_footer.is_none() {
                    sample_footer = Some(json!({"slot": f.slot, "producer_time_ns": f.block_producer_time_nanos,
                        "user_agent": String::from_utf8_lossy(&f.block_user_agent)}));
                }
            }
            Some(UpdateOneof::Slot(_)) => n_slots += 1,
            _ => {}
        }
    }
    Ok(json!({"entries": n_entries, "footers": n_footers, "slot_updates": n_slots, "sample_entry": sample_entry, "sample_footer": sample_footer}))
}

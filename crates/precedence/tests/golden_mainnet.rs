//! Golden tests against real mainnet transactions (v1, v0, legacy) taken from
//! post-Alpenglow blocks: our wire format and cost arithmetic must agree with
//! what the chain itself reported.

use base64::Engine;
use precedence::{cost, tx};
use solana_message::VersionedMessage;

#[derive(serde::Deserialize)]
struct Fixture {
    slot: u64,
    version: serde_json::Value,
    tx_base64: String,
    signature: String,
    #[serde(rename = "costUnits")]
    cost_units: u64,
    #[serde(rename = "computeUnitsConsumed")]
    consumed: u64,
    fee: u64,
    #[serde(rename = "transactionConfig")]
    config: Option<serde_json::Value>,
}

fn fixtures() -> Vec<Fixture> {
    let raw = include_str!("fixtures/mainnet_txs.json");
    serde_json::from_str(raw).unwrap()
}

#[test]
fn wire_roundtrip_is_byte_exact() {
    for f in fixtures() {
        let bytes = base64::engine::general_purpose::STANDARD.decode(&f.tx_base64).unwrap();
        let t = tx::from_wire(&bytes).unwrap();
        assert_eq!(tx::to_wire(&t), bytes, "slot {} sig {}", f.slot, f.signature);
        assert_eq!(t.signatures[0].to_string(), f.signature);
        t.verify_and_hash_message().expect("signature verifies");
    }
}

#[test]
fn v1_config_and_fee_match_chain() {
    for f in fixtures().into_iter().filter(|f| f.version == 1) {
        let bytes = base64::engine::general_purpose::STANDARD.decode(&f.tx_base64).unwrap();
        let t = tx::from_wire(&bytes).unwrap();
        let VersionedMessage::V1(m) = &t.message else { panic!("not v1") };
        let c = f.config.as_ref().unwrap();
        assert_eq!(m.config.priority_fee.unwrap_or(0), c["priorityFee"].as_u64().unwrap_or(0));
        assert_eq!(m.config.compute_unit_limit.map(u64::from), c["computeUnitLimit"].as_u64());
        // fee = 5000 per signature + priorityFee, exactly.
        let b = cost::budget(&t);
        assert_eq!(f.fee, 5_000 * t.signatures.len() as u64 + b.priority_fee);
    }
}

#[test]
fn legacy_and_v0_fee_matches_budget() {
    for f in fixtures().into_iter().filter(|f| f.version != 1) {
        let bytes = base64::engine::general_purpose::STANDARD.decode(&f.tx_base64).unwrap();
        let t = tx::from_wire(&bytes).unwrap();
        let b = cost::budget(&t);
        assert_eq!(f.fee, 5_000 * t.signatures.len() as u64 + b.priority_fee, "sig {}", f.signature);
    }
}

/// `meta.costUnits` is the executed cost: static part + consumed CU + loaded
/// data cost of the bytes actually loaded. The residual after removing the
/// parts we model must therefore be a positive multiple of 8 (one 32 KiB step
/// costs 8) and no larger than the requested loaded-data limit allows.
#[test]
fn executed_cost_residual_is_loaded_data_steps() {
    for f in fixtures() {
        let bytes = base64::engine::general_purpose::STANDARD.decode(&f.tx_base64).unwrap();
        let t = tx::from_wire(&bytes).unwrap();
        let modeled = cost::static_cost(&t) + f.consumed;
        assert!(f.cost_units >= modeled, "sig {} cost {} < modeled {}", f.signature, f.cost_units, modeled);
        let residual = f.cost_units - modeled;
        let b = cost::budget(&t);
        let max_steps = (b.loaded_data_limit as u64).div_ceil(32 * 1024);
        assert_eq!(residual % 8, 0, "sig {} residual {}", f.signature, residual);
        assert!(residual >= 8 && residual / 8 <= max_steps, "sig {} residual {}", f.signature, residual);
    }
}

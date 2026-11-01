//! The leader's view of a transaction: what it costs a block and what it pays
//! the leader. Agave (`core/src/transaction_priority.rs`, `cost-model/`) and
//! Firedancer (`src/disco/pack/fd_pack_cost.h`) share this arithmetic.

use solana_message::VersionedMessage;
use solana_transaction::versioned::VersionedTransaction;

pub const SIGNATURE_COST: u64 = 720;
pub const WRITE_LOCK_COST: u64 = 300;
pub const INSTRUCTION_DATA_BYTES_PER_CU: u64 = 4;
pub const LOADED_DATA_COST_PER_32K: u64 = 8;
pub const BASE_FEE_PER_SIGNATURE: u64 = 5_000;
/// Half of the base fee is burned; the leader keeps the rest.
pub const LEADER_SHARE_PER_SIGNATURE: u64 = 2_500;
pub const DEFAULT_LOADED_DATA_LIMIT: u32 = 64 * 1024 * 1024;
pub const MAX_CU_LIMIT: u32 = 1_400_000;
pub const DEFAULT_BUILTIN_CU: u32 = 3_000;
pub const DEFAULT_PROGRAM_CU: u32 = 200_000;

const BUILTINS: &[&str] = &[
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "Vote111111111111111111111111111111111111111",
    "Stake11111111111111111111111111111111111111",
    "Config1111111111111111111111111111111111111",
    "AddressLookupTab1e1111111111111111111111111",
    "BPFLoader1111111111111111111111111111111111",
    "BPFLoader2111111111111111111111111111111111",
    "BPFLoaderUpgradeab1e11111111111111111111111",
    "LoaderV411111111111111111111111111111111111",
];

/// Budget a transaction requests: CU limit, loaded-data limit, priority fee.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Budget {
    pub cu_limit: u32,
    pub loaded_data_limit: u32,
    pub priority_fee: u64,
}

fn u32_at(d: &[u8]) -> Option<u32> {
    d.get(1..5).map(|b| u32::from_le_bytes(b.try_into().unwrap()))
}
fn u64_at(d: &[u8]) -> Option<u64> {
    d.get(1..9).map(|b| u64::from_le_bytes(b.try_into().unwrap()))
}

pub fn budget(tx: &VersionedTransaction) -> Budget {
    let keys = tx.message.static_account_keys();
    if let VersionedMessage::V1(m) = &tx.message {
        return Budget {
            cu_limit: m.config.compute_unit_limit.unwrap_or(0),
            loaded_data_limit: m.config.loaded_accounts_data_size_limit.unwrap_or(0),
            priority_fee: m.config.priority_fee.unwrap_or(0),
        };
    }
    let (mut limit, mut price, mut loaded) = (None, 0u64, None);
    let mut default_cu: u64 = 0;
    for ix in tx.message.instructions() {
        let program = keys.get(ix.program_id_index as usize).map(|k| k.to_string()).unwrap_or_default();
        if program == crate::tx::COMPUTE_BUDGET_PROGRAM {
            match ix.data.first() {
                Some(2) => limit = u32_at(&ix.data),
                Some(3) => price = u64_at(&ix.data).unwrap_or(0),
                Some(4) => loaded = u32_at(&ix.data),
                _ => {}
            }
        } else if BUILTINS.contains(&program.as_str()) {
            default_cu += DEFAULT_BUILTIN_CU as u64;
        } else {
            default_cu += DEFAULT_PROGRAM_CU as u64;
        }
    }
    let cu_limit = limit.unwrap_or(default_cu.min(MAX_CU_LIMIT as u64) as u32).min(MAX_CU_LIMIT);
    Budget {
        cu_limit,
        loaded_data_limit: loaded.unwrap_or(DEFAULT_LOADED_DATA_LIMIT),
        priority_fee: crate::tx::legacy_priority_fee(price, cu_limit),
    }
}

pub fn num_write_locks(tx: &VersionedTransaction) -> u64 {
    let n = tx.message.static_account_keys().len();
    (0..n).filter(|&i| tx.message.is_maybe_writable_with_reserved_addresses(i, None::<&std::collections::HashSet<solana_address::Address>>)).count() as u64
}

pub fn instruction_data_bytes(tx: &VersionedTransaction) -> u64 {
    tx.message.instructions().iter().map(|ix| ix.data.len() as u64).sum()
}

fn loaded_data_cost(bytes: u32) -> u64 {
    (bytes as u64).div_ceil(32 * 1024) * LOADED_DATA_COST_PER_32K
}

/// Everything in the cost except execution and loaded data.
pub fn static_cost(tx: &VersionedTransaction) -> u64 {
    tx.signatures.len() as u64 * SIGNATURE_COST
        + num_write_locks(tx) * WRITE_LOCK_COST
        + instruction_data_bytes(tx) / INSTRUCTION_DATA_BYTES_PER_CU
}

/// Cost the scheduler sees before execution (requested limits).
pub fn estimated_cost(tx: &VersionedTransaction) -> u64 {
    let b = budget(tx);
    static_cost(tx) + b.cu_limit as u64 + loaded_data_cost(b.loaded_data_limit)
}

/// What the leader earns: priority fee plus the unburned half of base fees.
pub fn leader_reward(priority_fee: u64, n_sigs: u64) -> u64 {
    priority_fee + LEADER_SHARE_PER_SIGNATURE * n_sigs
}

/// Agave's ordering key: reward per unit of cost, scaled by 1e6.
pub fn agave_priority(priority_fee: u64, n_sigs: u64, cost: u64) -> u64 {
    ((leader_reward(priority_fee, n_sigs) as u128 * 1_000_000u128) / (cost as u128 + 1)) as u64
}

pub fn priority_of(tx: &VersionedTransaction) -> u64 {
    let b = budget(tx);
    agave_priority(b.priority_fee, tx.signatures.len() as u64, estimated_cost(tx))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tx::*;
    use solana_hash::Hash;
    use solana_keypair::Keypair;
    use solana_signer::Signer;

    fn probe(format: TxFormat, fee: u64) -> VersionedTransaction {
        let kp = Keypair::new();
        let spec = ProbeSpec {
            payer: kp.pubkey(),
            priority_fee: fee,
            cu_limit: 7_000,
            loaded_data_limit: 16_384,
            tag: make_tag(1, 1, 1),
            format,
            shared_write: None,
        };
        build_probe(&spec, Hash::new_from_array(rand::random()), &kp).unwrap()
    }

    #[test]
    fn v1_and_legacy_probes_have_near_equal_priority() {
        for fee in [0u64, 25, 100, 400, 1500, 5000, 15000, 40000] {
            let a = priority_of(&probe(TxFormat::V1, fee)) as f64;
            let b = priority_of(&probe(TxFormat::Legacy, fee)) as f64;
            let rel = (a - b).abs() / a.max(1.0);
            assert!(rel < 0.01, "fee {fee}: v1 {a} legacy {b} rel {rel}");
            assert_eq!(budget(&probe(TxFormat::Legacy, fee)).priority_fee, fee);
        }
    }

    #[test]
    fn priority_is_monotone_in_fee() {
        let mut last = 0;
        for fee in [0u64, 25, 100, 400, 1500, 5000, 15000, 40000] {
            let p = priority_of(&probe(TxFormat::V1, fee));
            assert!(p > last || fee == 0);
            last = p;
        }
    }

    #[test]
    fn v1_probe_cost_breakdown() {
        let tx = probe(TxFormat::V1, 0);
        // 1 signature, 1 writable (payer), 12 bytes of memo, 7000 CU, 16 KiB loaded.
        assert_eq!(estimated_cost(&tx), 720 + 300 + 12 / 4 + 7_000 + 8);
    }
}

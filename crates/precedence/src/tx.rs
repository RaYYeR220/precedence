//! Probe transactions.
//!
//! A probe is the smallest transaction that still carries everything the
//! experiment varies: who pays (and therefore which account is write-locked),
//! how much priority fee it offers, which wire format it uses, and a 12-byte
//! memo tag that makes every probe unique and readable on any explorer.

use solana_address::Address;
use solana_hash::Hash;
use solana_instruction::{AccountMeta, Instruction};
use solana_keypair::Keypair;
use solana_message::{legacy, v1, VersionedMessage};
use solana_signer::Signer;
use solana_transaction::versioned::VersionedTransaction;
use std::str::FromStr;

/// SPL Memo v2, copied from the account keys of a live mainnet block.
pub const MEMO_PROGRAM: &str = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";
pub const COMPUTE_BUDGET_PROGRAM: &str = "ComputeBudget111111111111111111111111111111";
pub const SYSTEM_PROGRAM: &str = "11111111111111111111111111111111";

pub const TAG_LEN: usize = 12;

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TxFormat {
    V1,
    Legacy,
}

#[derive(Debug, Clone)]
pub struct ProbeSpec {
    pub payer: Address,
    /// Total priority fee in lamports (v1 `priorityFee`; legacy gets the
    /// equivalent compute-unit price).
    pub priority_fee: u64,
    pub cu_limit: u32,
    pub loaded_data_limit: u32,
    pub tag: [u8; TAG_LEN],
    pub format: TxFormat,
    /// When set, the probe also write-locks this account (0-lamport transfer).
    pub shared_write: Option<Address>,
}

pub fn memo_program() -> Address {
    Address::from_str(MEMO_PROGRAM).expect("memo id")
}
pub fn compute_budget_program() -> Address {
    Address::from_str(COMPUTE_BUDGET_PROGRAM).expect("compute budget id")
}
pub fn system_program() -> Address {
    Address::from_str(SYSTEM_PROGRAM).expect("system id")
}

/// Compute-unit price (micro-lamports per CU) that makes a legacy probe pay
/// exactly `priority_fee` lamports: the runtime charges
/// `ceil(price * limit / 1e6)`, so we take the smallest price that reaches it.
pub fn legacy_cu_price(priority_fee: u64, cu_limit: u32) -> u64 {
    if priority_fee == 0 || cu_limit == 0 {
        return 0;
    }
    let num = priority_fee as u128 * 1_000_000u128;
    let limit = cu_limit as u128;
    // smallest p with ceil(p*limit/1e6) >= priority_fee
    let mut p = num / limit;
    while (p * limit).div_ceil(1_000_000) < priority_fee as u128 {
        p += 1;
    }
    while p > 0 && ((p - 1) * limit).div_ceil(1_000_000) >= priority_fee as u128 {
        p -= 1;
    }
    p as u64
}

/// The lamports a legacy probe actually pays as priority fee.
pub fn legacy_priority_fee(cu_price: u64, cu_limit: u32) -> u64 {
    (cu_price as u128 * cu_limit as u128).div_ceil(1_000_000) as u64
}

fn payload_instructions(spec: &ProbeSpec) -> Vec<Instruction> {
    let mut ixs = Vec::with_capacity(2);
    if let Some(shared) = spec.shared_write {
        // System transfer of 0 lamports: discriminant 2, u64 amount.
        let mut data = 2u32.to_le_bytes().to_vec();
        data.extend_from_slice(&0u64.to_le_bytes());
        ixs.push(Instruction {
            program_id: system_program(),
            accounts: vec![AccountMeta::new(spec.payer, true), AccountMeta::new(shared, false)],
            data,
        });
    }
    ixs.push(Instruction { program_id: memo_program(), accounts: vec![], data: spec.tag.to_vec() });
    ixs
}

pub fn build_probe(spec: &ProbeSpec, blockhash: Hash, signer: &Keypair) -> anyhow::Result<VersionedTransaction> {
    anyhow::ensure!(signer.pubkey() == spec.payer, "signer is not the payer");
    anyhow::ensure!(spec.cu_limit > 0, "cu_limit must be set (0 fails on chain)");
    anyhow::ensure!(spec.loaded_data_limit > 0, "loaded_data_limit must be set (0 fails on chain)");
    let payload = payload_instructions(spec);
    let message = match spec.format {
        TxFormat::V1 => {
            let config = v1::TransactionConfig::empty()
                .with_priority_fee(spec.priority_fee)
                .with_compute_unit_limit(spec.cu_limit)
                .with_loaded_accounts_data_size_limit(spec.loaded_data_limit);
            VersionedMessage::V1(v1::Message::try_compile_with_config(&spec.payer, &payload, blockhash, config)?)
        }
        TxFormat::Legacy => {
            let cb = compute_budget_program();
            let mut ixs = Vec::with_capacity(payload.len() + 3);
            let mut limit = vec![2u8];
            limit.extend_from_slice(&spec.cu_limit.to_le_bytes());
            ixs.push(Instruction { program_id: cb, accounts: vec![], data: limit });
            let price = legacy_cu_price(spec.priority_fee, spec.cu_limit);
            if price > 0 {
                let mut p = vec![3u8];
                p.extend_from_slice(&price.to_le_bytes());
                ixs.push(Instruction { program_id: cb, accounts: vec![], data: p });
            }
            let mut loaded = vec![4u8];
            loaded.extend_from_slice(&spec.loaded_data_limit.to_le_bytes());
            ixs.push(Instruction { program_id: cb, accounts: vec![], data: loaded });
            ixs.extend(payload);
            VersionedMessage::Legacy(legacy::Message::new_with_blockhash(&ixs, Some(&spec.payer), &blockhash))
        }
    };
    Ok(VersionedTransaction::try_new(message, &[signer])?)
}

pub fn to_wire(tx: &VersionedTransaction) -> Vec<u8> {
    wincode::serialize(tx).expect("serialize transaction")
}

pub fn from_wire(bytes: &[u8]) -> anyhow::Result<VersionedTransaction> {
    Ok(wincode::deserialize(bytes)?)
}

/// Unique, fixed-length, human-readable tag: `p` + 5 base36 chars of the run
/// + 4 of the batch + 2 of the probe index. Fixed length keeps every probe's
///   compute cost identical.
pub fn make_tag(run: u32, batch: u32, index: u16) -> [u8; TAG_LEN] {
    fn b36(mut v: u64, width: usize, out: &mut Vec<u8>) {
        const D: &[u8] = b"0123456789abcdefghijklmnopqrstuvwxyz";
        let mut buf = vec![b'0'; width];
        for i in (0..width).rev() {
            buf[i] = D[(v % 36) as usize];
            v /= 36;
        }
        out.extend_from_slice(&buf);
    }
    let mut s = vec![b'p'];
    b36(run as u64 % 36u64.pow(5), 5, &mut s);
    b36(batch as u64 % 36u64.pow(4), 4, &mut s);
    b36(index as u64 % 36u64.pow(2), 2, &mut s);
    let mut tag = [0u8; TAG_LEN];
    tag.copy_from_slice(&s);
    tag
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(format: TxFormat, fee: u64, shared: Option<Address>, kp: &Keypair) -> ProbeSpec {
        ProbeSpec {
            payer: kp.pubkey(),
            priority_fee: fee,
            cu_limit: 7_000,
            loaded_data_limit: 16_384,
            tag: make_tag(1, 2, 3),
            format,
            shared_write: shared,
        }
    }

    #[test]
    fn v1_probe_fields() {
        let kp = Keypair::new();
        let tx = build_probe(&spec(TxFormat::V1, 1234, None, &kp), Hash::new_from_array(rand::random()), &kp).unwrap();
        let back = from_wire(&to_wire(&tx)).unwrap();
        match &back.message {
            VersionedMessage::V1(m) => {
                assert_eq!(m.config.priority_fee, Some(1234));
                assert_eq!(m.config.compute_unit_limit, Some(7_000));
                assert_eq!(m.config.loaded_accounts_data_size_limit, Some(16_384));
                assert_eq!(m.instructions.len(), 1);
            }
            other => panic!("expected v1, got {other:?}"),
        }
        back.verify_and_hash_message().expect("valid signature");
    }

    #[test]
    fn shared_write_locks_the_shared_account() {
        let kp = Keypair::new();
        let shared = Address::new_from_array(rand::random());
        let tx = build_probe(&spec(TxFormat::V1, 0, Some(shared), &kp), Hash::new_from_array(rand::random()), &kp).unwrap();
        let keys = tx.message.static_account_keys();
        let idx = keys.iter().position(|k| *k == shared).unwrap();
        assert!(tx.message.is_maybe_writable_with_reserved_addresses(idx, None::<&std::collections::HashSet<Address>>));
    }

    #[test]
    fn legacy_pays_exact_fee() {
        for fee in [1u64, 25, 100, 400, 1500, 5000, 15000, 40000] {
            for limit in [150u32, 6_500, 7_000] {
                let p = legacy_cu_price(fee, limit);
                assert_eq!(legacy_priority_fee(p, limit), fee, "fee {fee} limit {limit}");
            }
        }
        assert_eq!(legacy_cu_price(0, 7000), 0);
    }

    #[test]
    fn tags_unique_and_fixed_length() {
        let mut seen = std::collections::HashSet::new();
        for b in 0..500u32 {
            for i in 0..20u16 {
                let t = make_tag(7, b, i);
                assert_eq!(t.len(), TAG_LEN);
                assert!(t.iter().all(|c| c.is_ascii_alphanumeric()));
                assert!(seen.insert(t));
            }
        }
    }

    #[test]
    fn rejects_zero_limits() {
        let kp = Keypair::new();
        let mut s = spec(TxFormat::V1, 0, None, &kp);
        s.cu_limit = 0;
        assert!(build_probe(&s, Hash::new_from_array(rand::random()), &kp).is_err());
    }
}

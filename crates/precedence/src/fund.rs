//! Funding the payer pool from the operator key, and sweeping it back.
//! Both go through the spend ledger like probes do.

use crate::cost::BASE_FEE_PER_SIGNATURE;
use crate::keys;
use crate::ledger::Ledger;
use crate::rpc::Rpc;
use anyhow::{bail, Context, Result};
use base64::Engine;
use serde_json::json;
use solana_address::Address;
use solana_hash::Hash;
use solana_instruction::{AccountMeta, Instruction};
use solana_keypair::Keypair;
use solana_message::{legacy, VersionedMessage};
use solana_signer::Signer;
use solana_transaction::versioned::VersionedTransaction;
use std::str::FromStr;
use std::time::Duration;

/// Below this a system account cannot exist (rent-exempt minimum, 0 data).
pub const RENT_EXEMPT_MIN: u64 = 890_880;

pub fn transfer_ix(from: &Address, to: &Address, lamports: u64) -> Instruction {
    let mut data = 2u32.to_le_bytes().to_vec();
    data.extend_from_slice(&lamports.to_le_bytes());
    Instruction {
        program_id: crate::tx::system_program(),
        accounts: vec![AccountMeta::new(*from, true), AccountMeta::new(*to, false)],
        data,
    }
}

fn sign(ixs: &[Instruction], payer: &Keypair, bh: Hash) -> Result<VersionedTransaction> {
    let msg = legacy::Message::new_with_blockhash(ixs, Some(&payer.pubkey()), &bh);
    Ok(VersionedTransaction::try_new(VersionedMessage::Legacy(msg), &[payer])?)
}

async fn send_and_confirm(rpc: &Rpc, tx: &VersionedTransaction) -> Result<String> {
    let wire = base64::engine::general_purpose::STANDARD.encode(crate::tx::to_wire(tx));
    let sig = rpc
        .call("sendTransaction", json!([wire, {"encoding": "base64", "preflightCommitment": "confirmed"}]))
        .await?
        .as_str()
        .context("signature")?
        .to_string();
    for _ in 0..90 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        let st = rpc.signature_statuses(std::slice::from_ref(&sig)).await?;
        if let Some(s) = st.first().filter(|s| !s.is_null()) {
            if !s["err"].is_null() {
                bail!("{sig} failed: {}", s["err"]);
            }
            if matches!(s["confirmationStatus"].as_str(), Some("confirmed" | "finalized")) {
                return Ok(sig);
            }
        }
    }
    bail!("{sig} not confirmed in 90 s")
}

/// Top every payer up to `target` lamports in one transaction from `master`.
pub async fn fund(rpc: &Rpc, ledger: &mut Ledger, master: &Keypair, n: u16, target: u64, dry: bool) -> Result<serde_json::Value> {
    anyhow::ensure!(target >= RENT_EXEMPT_MIN, "target below rent-exempt minimum");
    let payers = keys::derive_payers(master, n);
    let mut ixs = Vec::new();
    let mut plan = Vec::new();
    for p in &payers {
        let bal = rpc.balance(&p.pubkey().to_string()).await?;
        if bal < target {
            ixs.push(transfer_ix(&master.pubkey(), &p.pubkey(), target - bal));
            plan.push(json!({"payer": p.pubkey().to_string(), "balance": bal, "top_up": target - bal}));
        }
    }
    let total: u64 = plan.iter().map(|p| p["top_up"].as_u64().unwrap()).sum();
    let master_bal = rpc.balance(&master.pubkey().to_string()).await?;
    let mut sigs = Vec::new();
    if !ixs.is_empty() && !dry {
        anyhow::ensure!(master_bal >= total + 20_000, "master holds {master_bal}, needs {total} + fees");
        for chunk in ixs.chunks(16) {
            let (bh, _) = rpc.latest_blockhash().await?;
            let tx = sign(chunk, master, Hash::from_str(&bh)?)?;
            let sig = tx.signatures[0].to_string();
            ledger.reserve("fund", None, &[(sig.clone(), BASE_FEE_PER_SIGNATURE)])?;
            let sig = send_and_confirm(rpc, &tx).await?;
            ledger.settle("fund", &sig, BASE_FEE_PER_SIGNATURE)?;
            sigs.push(sig);
        }
    }
    Ok(json!({"master": master.pubkey().to_string(), "master_balance": master_bal, "payers": plan, "total_top_up": total, "signatures": sigs, "dry": dry}))
}

/// Return every payer's balance to `master`; the payer accounts close.
pub async fn sweep(rpc: &Rpc, ledger: &mut Ledger, master: &Keypair, n: u16, dry: bool) -> Result<serde_json::Value> {
    let mut out = Vec::new();
    for p in keys::derive_payers(master, n) {
        let bal = rpc.balance(&p.pubkey().to_string()).await?;
        if bal <= BASE_FEE_PER_SIGNATURE {
            continue;
        }
        let amount = bal - BASE_FEE_PER_SIGNATURE;
        let mut row = json!({"payer": p.pubkey().to_string(), "balance": bal, "returned": amount});
        if !dry {
            let (bh, _) = rpc.latest_blockhash().await?;
            let tx = sign(&[transfer_ix(&p.pubkey(), &master.pubkey(), amount)], &p, Hash::from_str(&bh)?)?;
            ledger.reserve("sweep", None, &[(tx.signatures[0].to_string(), BASE_FEE_PER_SIGNATURE)])?;
            let sig = send_and_confirm(rpc, &tx).await?;
            ledger.settle("sweep", &sig, BASE_FEE_PER_SIGNATURE)?;
            row["signature"] = json!(sig);
        }
        out.push(row);
    }
    Ok(json!({"master": master.pubkey().to_string(), "swept": out, "dry": dry}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transfer_layout() {
        let a = Address::new_from_array([1; 32]);
        let b = Address::new_from_array([2; 32]);
        let ix = transfer_ix(&a, &b, 5);
        assert_eq!(ix.data, [2, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0]);
        assert!(ix.accounts[0].is_signer && ix.accounts[0].is_writable && ix.accounts[1].is_writable);
    }
}

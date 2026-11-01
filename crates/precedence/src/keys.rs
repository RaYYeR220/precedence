//! Keys. The only secret is the operator's keypair, mounted read-only. Probe
//! payers are derived from it deterministically, so nothing else needs to be
//! stored and `sweep` can always find them again.

use anyhow::{anyhow, Context, Result};
use sha2::{Digest, Sha256};
use solana_address::Address;
use solana_keypair::Keypair;
use solana_signer::Signer;
use std::path::Path;

pub fn load_keypair(path: &Path) -> Result<Keypair> {
    solana_keypair::read_keypair_file(path).map_err(|e| anyhow!("{e}")).with_context(|| format!("reading {}", path.display()))
}

/// Payer `i` of the pool derived from `master`.
pub fn derive_payer(master: &Keypair, i: u16) -> Keypair {
    let mut h = Sha256::new();
    h.update(b"precedence/payer/v1");
    h.update(master.secret_bytes());
    h.update(i.to_le_bytes());
    Keypair::new_from_array(h.finalize().into())
}

pub fn derive_payers(master: &Keypair, n: u16) -> Vec<Keypair> {
    (0..n).map(|i| derive_payer(master, i)).collect()
}

/// The account every probe of a `shared` batch write-locks. It is never
/// funded and never signs: it exists only as a lock.
pub fn shared_sink(run_id: &str, batch_id: u32) -> Address {
    let mut h = Sha256::new();
    h.update(b"precedence/sink/v1");
    h.update(run_id.as_bytes());
    h.update(batch_id.to_le_bytes());
    Address::new_from_array(h.finalize().into())
}

/// Throwaway keys for dry runs without a mounted keypair.
pub fn ephemeral_master() -> Keypair {
    Keypair::new()
}

pub fn pubkey_str(k: &Keypair) -> String {
    k.pubkey().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn payers_are_deterministic_and_distinct() {
        let m = Keypair::new_from_array([7u8; 32]);
        let a = derive_payers(&m, 16);
        let b = derive_payers(&m, 16);
        assert_eq!(a.iter().map(|k| k.pubkey()).collect::<Vec<_>>(), b.iter().map(|k| k.pubkey()).collect::<Vec<_>>());
        let mut set: Vec<_> = a.iter().map(|k| k.pubkey()).collect();
        set.sort();
        set.dedup();
        assert_eq!(set.len(), 16);
        assert!(!set.contains(&m.pubkey()));
        let other = derive_payers(&Keypair::new_from_array([8u8; 32]), 16);
        assert_ne!(other[0].pubkey(), a[0].pubkey());
    }

    #[test]
    fn sinks_differ_per_batch() {
        assert_ne!(shared_sink("r", 1), shared_sink("r", 2));
        assert_eq!(shared_sink("r", 1), shared_sink("r", 1));
    }
}

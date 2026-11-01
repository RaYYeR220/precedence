//! Spend ledger: the cap is enforced before a transaction exists on the wire.
//!
//! Every live transaction (probe, funding transfer, sweep) is reserved at its
//! worst-case fee and the reservation is fsynced before the transaction leaves
//! the process. A batch is reserved as a whole or not at all. The ledger is an
//! append-only JSONL file; reopening it recomputes the totals, and the cap
//! stored in it can only go down.

use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

pub const LAMPORTS_PER_SOL: u64 = 1_000_000_000;
/// Default cap: 0.05 SOL, as the study budget.
pub const DEFAULT_CAP: u64 = 50_000_000;
/// No configuration may raise the cap above 0.06 SOL.
pub const HARD_MAX_CAP: u64 = 60_000_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EntryKind {
    Cap,
    Reserve,
    Settle,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entry {
    pub ts_unix_us: u64,
    pub kind: EntryKind,
    pub run_id: String,
    /// `probe`, `fund`, `sweep`.
    pub purpose: String,
    pub batch_id: Option<u32>,
    pub signature: Option<String>,
    /// Reserve: worst-case fee. Settle: fee actually charged on chain (0 if it never landed).
    pub lamports: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum LedgerError {
    #[error("spend cap exceeded: reserved {reserved} + batch {batch} > cap {cap} lamports")]
    CapExceeded { reserved: u64, batch: u64, cap: u64 },
    #[error("requested cap {0} lamports is above the hard maximum {HARD_MAX_CAP}")]
    AboveHardMax(u64),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Totals {
    pub cap: u64,
    /// Sum of worst-case reservations of everything ever sent.
    pub reserved: u64,
    /// Sum of fees actually charged, for settled entries.
    pub settled: u64,
    pub n_reserved: u64,
    pub n_settled: u64,
    /// What counts against the cap: fees settled plus the worst case of every
    /// reservation not settled yet. A settled transaction's actual fee never
    /// exceeds its reservation, so this bound is never below what was spent.
    pub committed: u64,
}

pub struct Ledger {
    path: PathBuf,
    file: File,
    totals: Totals,
    run_id: String,
    open: std::collections::HashMap<String, u64>,
}

pub fn now_us() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_micros() as u64
}

impl Ledger {
    /// Open (or create) the ledger. The effective cap is the lower of the
    /// stored cap and `requested_cap`.
    pub fn open(path: impl AsRef<Path>, requested_cap: u64, run_id: &str) -> Result<Self, LedgerError> {
        if requested_cap > HARD_MAX_CAP {
            return Err(LedgerError::AboveHardMax(requested_cap));
        }
        let path = path.as_ref().to_path_buf();
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let mut totals = Totals { cap: requested_cap, ..Default::default() };
        let mut stored_cap: Option<u64> = None;
        let mut open: std::collections::HashMap<String, u64> = std::collections::HashMap::new();
        if path.exists() {
            for line in BufReader::new(File::open(&path)?).lines() {
                let line = line?;
                if line.trim().is_empty() {
                    continue;
                }
                let e: Entry = serde_json::from_str(&line)?;
                match e.kind {
                    EntryKind::Cap => stored_cap = Some(stored_cap.map_or(e.lamports, |c| c.min(e.lamports))),
                    EntryKind::Reserve => {
                        totals.reserved += e.lamports;
                        totals.n_reserved += 1;
                        if let Some(sig) = &e.signature {
                            *open.entry(sig.clone()).or_default() += e.lamports;
                        }
                    }
                    EntryKind::Settle => {
                        totals.settled += e.lamports;
                        totals.n_settled += 1;
                        if let Some(sig) = &e.signature {
                            open.remove(sig);
                        }
                    }
                }
            }
        }
        totals.cap = stored_cap.map_or(requested_cap, |c| c.min(requested_cap));
        totals.committed = totals.settled + open.values().sum::<u64>();
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        let mut ledger = Ledger { path, file, totals, run_id: run_id.to_string(), open };
        if stored_cap != Some(ledger.totals.cap) {
            let cap = ledger.totals.cap;
            ledger.append(&[Entry {
                ts_unix_us: now_us(),
                kind: EntryKind::Cap,
                run_id: run_id.to_string(),
                purpose: "cap".into(),
                batch_id: None,
                signature: None,
                lamports: cap,
            }])?;
        }
        Ok(ledger)
    }

    fn append(&mut self, entries: &[Entry]) -> Result<(), LedgerError> {
        let mut buf = Vec::new();
        for e in entries {
            serde_json::to_writer(&mut buf, e)?;
            buf.push(b'\n');
        }
        self.file.write_all(&buf)?;
        self.file.sync_all()?;
        Ok(())
    }

    pub fn totals(&self) -> Totals {
        self.totals
    }

    pub fn remaining(&self) -> u64 {
        self.totals.cap.saturating_sub(self.totals.committed)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Reserve a whole batch: `(signature, worst_case_lamports)`. All or nothing.
    pub fn reserve(&mut self, purpose: &str, batch_id: Option<u32>, items: &[(String, u64)]) -> Result<(), LedgerError> {
        let batch: u64 = items.iter().map(|i| i.1).sum();
        if self.totals.committed + batch > self.totals.cap {
            return Err(LedgerError::CapExceeded { reserved: self.totals.committed, batch, cap: self.totals.cap });
        }
        let ts = now_us();
        let entries: Vec<Entry> = items
            .iter()
            .map(|(sig, l)| Entry {
                ts_unix_us: ts,
                kind: EntryKind::Reserve,
                run_id: self.run_id.clone(),
                purpose: purpose.into(),
                batch_id,
                signature: Some(sig.clone()),
                lamports: *l,
            })
            .collect();
        self.append(&entries)?;
        self.totals.reserved += batch;
        self.totals.committed += batch;
        self.totals.n_reserved += items.len() as u64;
        for (sig, l) in items {
            *self.open.entry(sig.clone()).or_default() += *l;
        }
        Ok(())
    }

    /// Record what a transaction actually cost (0 if it never landed).
    pub fn settle(&mut self, purpose: &str, signature: &str, lamports: u64) -> Result<(), LedgerError> {
        self.append(&[Entry {
            ts_unix_us: now_us(),
            kind: EntryKind::Settle,
            run_id: self.run_id.clone(),
            purpose: purpose.into(),
            batch_id: None,
            signature: Some(signature.into()),
            lamports,
        }])?;
        self.totals.settled += lamports;
        self.totals.n_settled += 1;
        let released = self.open.remove(signature).unwrap_or(0);
        self.totals.committed = self.totals.committed - released + lamports;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("precedence-ledger-{name}-{}.jsonl", rand::random::<u64>()));
        let _ = std::fs::remove_file(&p);
        p
    }

    #[test]
    fn over_cap_reserves_nothing() {
        let p = tmp("over");
        let mut l = Ledger::open(&p, 20_000, "r").unwrap();
        l.reserve("probe", Some(0), &[("a".into(), 9_000), ("b".into(), 9_000)]).unwrap();
        let err = l.reserve("probe", Some(1), &[("c".into(), 1_000), ("d".into(), 1_500)]).unwrap_err();
        assert!(matches!(err, LedgerError::CapExceeded { .. }));
        assert_eq!(l.totals().reserved, 18_000);
        drop(l);
        let l = Ledger::open(&p, 20_000, "r").unwrap();
        assert_eq!(l.totals().reserved, 18_000);
        assert_eq!(l.totals().n_reserved, 2);
    }

    #[test]
    fn cap_can_only_go_down() {
        let p = tmp("cap");
        drop(Ledger::open(&p, 30_000_000, "r").unwrap());
        let l = Ledger::open(&p, 50_000_000, "r").unwrap();
        assert_eq!(l.totals().cap, 30_000_000);
        drop(l);
        let l = Ledger::open(&p, 10_000_000, "r").unwrap();
        assert_eq!(l.totals().cap, 10_000_000);
        drop(l);
        assert_eq!(Ledger::open(&p, 50_000_000, "r").unwrap().totals().cap, 10_000_000);
    }

    #[test]
    fn settling_releases_the_unspent_worst_case() {
        let p = tmp("release");
        let mut l = Ledger::open(&p, 20_000, "r").unwrap();
        l.reserve("probe", Some(0), &[("a".into(), 9_000), ("b".into(), 9_000)]).unwrap();
        assert!(l.reserve("probe", Some(1), &[("c".into(), 9_000)]).is_err());
        l.settle("probe", "a", 5_000).unwrap(); // landed, cheaper than its worst case
        l.settle("probe", "b", 0).unwrap(); // never landed
        assert_eq!(l.totals().committed, 5_000);
        l.reserve("probe", Some(1), &[("c".into(), 9_000)]).unwrap();
        drop(l);
        let t = Ledger::open(&p, 20_000, "r").unwrap().totals();
        assert_eq!((t.committed, t.settled, t.reserved), (14_000, 5_000, 27_000));
    }

    #[test]
    fn hard_max_is_refused() {
        assert!(matches!(Ledger::open(tmp("max"), HARD_MAX_CAP + 1, "r"), Err(LedgerError::AboveHardMax(_))));
    }

    #[test]
    fn settle_is_tracked_and_survives_reopen() {
        let p = tmp("settle");
        let mut l = Ledger::open(&p, 1_000_000, "r").unwrap();
        l.reserve("fund", None, &[("f".into(), 5_000)]).unwrap();
        l.settle("fund", "f", 5_000).unwrap();
        drop(l);
        let t = Ledger::open(&p, 1_000_000, "r").unwrap().totals();
        assert_eq!((t.reserved, t.settled, t.n_settled), (5_000, 5_000, 1));
    }
}

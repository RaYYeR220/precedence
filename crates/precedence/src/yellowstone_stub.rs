//! The entry recorder needs the Linux build (see the Dockerfile).

use crate::clock::TimeBase;
use anyhow::{bail, Result};
use std::path::PathBuf;

pub type Wanted = std::sync::Arc<std::sync::Mutex<std::collections::BTreeSet<u64>>>;

pub async fn record(
    _url: String,
    _token: Option<String>,
    _wanted: Wanted,
    _run_dir: PathBuf,
    _tb: TimeBase,
    _clock: Option<crate::clock::SharedClock>,
) -> Result<()> {
    bail!("the entry recorder is only built on Unix targets; run it in the container")
}

pub async fn probe(_url: &str, _token: Option<String>, _seconds: u64) -> Result<serde_json::Value> {
    bail!("the entry recorder is only built on Unix targets; run it in the container")
}

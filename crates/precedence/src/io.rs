//! Small file helpers shared by the run, the collector and the recorder.

use anyhow::Result;
use serde::Serialize;
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Read, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};

/// Append-only JSON-lines writer, cheap to clone, flushed on every line so a
/// crash never loses what was already recorded.
#[derive(Clone)]
pub struct JsonlWriter {
    inner: Arc<Mutex<BufWriter<File>>>,
}

impl JsonlWriter {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        if let Some(d) = path.as_ref().parent() {
            std::fs::create_dir_all(d)?;
        }
        let f = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(JsonlWriter { inner: Arc::new(Mutex::new(BufWriter::new(f))) })
    }

    pub fn write<T: Serialize>(&self, value: &T) {
        let mut line = serde_json::to_vec(value).expect("serialize jsonl line");
        line.push(b'\n');
        let mut w = self.inner.lock().unwrap();
        if let Err(e) = w.write_all(&line).and_then(|_| w.flush()) {
            tracing::error!(error = %e, "jsonl write failed");
        }
    }
}

pub fn write_json<T: Serialize>(path: impl AsRef<Path>, value: &T) -> Result<()> {
    if let Some(d) = path.as_ref().parent() {
        std::fs::create_dir_all(d)?;
    }
    let mut v = serde_json::to_vec_pretty(value)?;
    v.push(b'\n');
    std::fs::write(path, v)?;
    Ok(())
}

pub fn write_json_gz<T: Serialize>(path: impl AsRef<Path>, value: &T) -> Result<()> {
    if let Some(d) = path.as_ref().parent() {
        std::fs::create_dir_all(d)?;
    }
    let f = File::create(path)?;
    // Fixed mtime/no name in the header so identical content gives identical bytes.
    let mut gz = flate2::GzBuilder::new().mtime(0).write(f, flate2::Compression::best());
    gz.write_all(&serde_json::to_vec(value)?)?;
    gz.finish()?;
    Ok(())
}

pub fn read_json_gz(path: impl AsRef<Path>) -> Result<serde_json::Value> {
    let mut s = Vec::new();
    flate2::read::GzDecoder::new(File::open(path)?).read_to_end(&mut s)?;
    Ok(serde_json::from_slice(&s)?)
}

pub fn read_jsonl(path: impl AsRef<Path>) -> Result<Vec<serde_json::Value>> {
    let s = std::fs::read_to_string(path)?;
    Ok(s.lines().filter(|l| !l.trim().is_empty()).map(serde_json::from_str).collect::<Result<_, _>>()?)
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::Digest;
    hex::encode(sha2::Sha256::digest(bytes))
}

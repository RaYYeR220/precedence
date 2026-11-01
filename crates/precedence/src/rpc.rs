//! Minimal JSON-RPC client. Responses are kept as raw JSON so that what we
//! store is exactly what the node returned.

use anyhow::{anyhow, bail, Context, Result};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

#[derive(Clone)]
pub struct Rpc {
    url: String,
    http: reqwest::Client,
    limiter: Arc<Mutex<HashMap<String, Instant>>>,
    /// Minimum spacing between two calls of the same method.
    spacing: Arc<HashMap<&'static str, Duration>>,
    default_spacing: Duration,
}

#[derive(Debug, thiserror::Error)]
#[error("rpc error {code}: {message}")]
pub struct RpcError {
    pub code: i64,
    pub message: String,
}

impl Rpc {
    pub fn new(url: &str) -> Self {
        // reqwest is built without a bundled TLS provider; use ring everywhere.
        let _ = rustls::crypto::ring::default_provider().install_default();
        let mut spacing = HashMap::new();
        // The public mainnet endpoint limits getBlock hardest.
        spacing.insert("getBlock", Duration::from_millis(1_100));
        spacing.insert("getClusterNodes", Duration::from_millis(500));
        spacing.insert("getLeaderSchedule", Duration::from_millis(500));
        spacing.insert("getVoteAccounts", Duration::from_millis(500));
        Rpc {
            url: url.to_string(),
            http: reqwest::Client::builder().timeout(Duration::from_secs(60)).build().unwrap(),
            limiter: Arc::new(Mutex::new(HashMap::new())),
            spacing: Arc::new(spacing),
            default_spacing: Duration::from_millis(120),
        }
    }

    /// Override the minimum spacing of one method (e.g. a local node).
    pub fn with_spacing(mut self, method: &'static str, gap: Duration) -> Self {
        let mut m = (*self.spacing).clone();
        m.insert(method, gap);
        self.spacing = Arc::new(m);
        self
    }

    pub fn url(&self) -> &str {
        &self.url
    }

    async fn pace(&self, method: &str) {
        let gap = *self.spacing.get(method).unwrap_or(&self.default_spacing);
        let wait = {
            let mut m = self.limiter.lock().await;
            let now = Instant::now();
            let next = m.get(method).map(|t| *t + gap).unwrap_or(now);
            let start = next.max(now);
            m.insert(method.to_string(), start);
            start.saturating_duration_since(now)
        };
        if !wait.is_zero() {
            tokio::time::sleep(wait).await;
        }
    }

    /// Call `method`; retries transport errors and HTTP 429/5xx with backoff.
    /// JSON-RPC errors are returned as `RpcError` without retry.
    pub async fn call(&self, method: &str, params: Value) -> Result<Value> {
        let body = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params});
        let mut backoff = Duration::from_millis(500);
        for attempt in 0..8 {
            self.pace(method).await;
            let resp = self.http.post(&self.url).json(&body).send().await;
            match resp {
                Ok(r) if r.status().as_u16() == 429 || r.status().is_server_error() => {
                    let retry_after = r
                        .headers()
                        .get("retry-after")
                        .and_then(|v| v.to_str().ok())
                        .and_then(|v| v.parse::<u64>().ok())
                        .map(Duration::from_secs);
                    tracing::debug!(method, status = r.status().as_u16(), attempt, "rpc throttled");
                    tokio::time::sleep(retry_after.unwrap_or(backoff)).await;
                }
                Ok(r) => {
                    let v: Value = r.json().await.with_context(|| format!("{method}: bad json"))?;
                    if let Some(e) = v.get("error") {
                        let mut message = e["message"].as_str().unwrap_or("").to_string();
                        // Preflight failures carry the simulation's error and logs.
                        if let Some(d) = e.get("data").filter(|d| !d.is_null()) {
                            let err = &d["err"];
                            let logs: Vec<&str> = d["logs"].as_array().map(|l| l.iter().filter_map(|x| x.as_str()).collect()).unwrap_or_default();
                            message = format!("{message}; err={err}; logs={}", logs.join(" | "));
                        }
                        return Err(RpcError { code: e["code"].as_i64().unwrap_or(0), message }.into());
                    }
                    return v.get("result").cloned().ok_or_else(|| anyhow!("{method}: no result"));
                }
                Err(e) => {
                    tracing::debug!(method, attempt, error = %e, "rpc transport error");
                    tokio::time::sleep(backoff).await;
                }
            }
            backoff = (backoff * 2).min(Duration::from_secs(10));
        }
        bail!("{method}: gave up after retries")
    }

    pub async fn slot(&self, commitment: &str) -> Result<u64> {
        self.call("getSlot", json!([{"commitment": commitment}])).await?.as_u64().ok_or_else(|| anyhow!("slot"))
    }

    pub async fn latest_blockhash(&self) -> Result<(String, u64)> {
        let v = self.call("getLatestBlockhash", json!([{"commitment": "processed"}])).await?;
        Ok((
            v["value"]["blockhash"].as_str().ok_or_else(|| anyhow!("blockhash"))?.to_string(),
            v["context"]["slot"].as_u64().ok_or_else(|| anyhow!("context slot"))?,
        ))
    }

    pub async fn balance(&self, pubkey: &str) -> Result<u64> {
        let v = self.call("getBalance", json!([pubkey, {"commitment": "confirmed"}])).await?;
        v["value"].as_u64().ok_or_else(|| anyhow!("balance"))
    }

    /// `getBlock` with every field the study needs. `Ok(None)` for skipped or
    /// not-yet-available slots.
    pub async fn block(&self, slot: u64) -> Result<Option<Value>> {
        let params = json!([slot, {
            "encoding": "json",
            "maxSupportedTransactionVersion": 1,
            "transactionDetails": "full",
            "rewards": true,
            "commitment": "finalized"
        }]);
        match self.call("getBlock", params).await {
            Ok(v) => Ok(Some(v)),
            Err(e) => match e.downcast_ref::<RpcError>() {
                // -32004 block not available, -32007 skipped, -32009 missing in long-term storage
                Some(r) if matches!(r.code, -32004 | -32007 | -32009) => Ok(None),
                _ => Err(e),
            },
        }
    }

    /// `getBlock` with signatures only: what a judge needs to check positions.
    pub async fn block_signatures(&self, slot: u64) -> Result<Option<Value>> {
        let params = json!([slot, {
            "encoding": "json",
            "maxSupportedTransactionVersion": 1,
            "transactionDetails": "signatures",
            "rewards": true,
            "commitment": "finalized"
        }]);
        match self.call("getBlock", params).await {
            Ok(v) => Ok(Some(v)),
            Err(e) => match e.downcast_ref::<RpcError>() {
                Some(r) if matches!(r.code, -32004 | -32007 | -32009) => Ok(None),
                _ => Err(e),
            },
        }
    }

    pub async fn signature_statuses(&self, sigs: &[String]) -> Result<Vec<Value>> {
        let mut out = Vec::with_capacity(sigs.len());
        for chunk in sigs.chunks(256) {
            let v = self.call("getSignatureStatuses", json!([chunk, {"searchTransactionHistory": true}])).await?;
            out.extend(v["value"].as_array().cloned().unwrap_or_default());
        }
        Ok(out)
    }

    pub async fn send_transaction(&self, wire_base64: &str) -> Result<String> {
        let v = self
            .call(
                "sendTransaction",
                json!([wire_base64, {"encoding": "base64", "skipPreflight": true, "maxRetries": 0}]),
            )
            .await?;
        v.as_str().map(str::to_string).ok_or_else(|| anyhow!("signature"))
    }

    pub async fn simulate(&self, wire_base64: &str) -> Result<Value> {
        self.call(
            "simulateTransaction",
            json!([wire_base64, {"encoding": "base64", "sigVerify": false, "replaceRecentBlockhash": true, "commitment": "processed"}]),
        )
        .await
    }

    pub async fn accounts(&self, pubkeys: &[&str]) -> Result<Vec<Value>> {
        let v = self.call("getMultipleAccounts", json!([pubkeys, {"encoding": "base64"}])).await?;
        Ok(v["value"].as_array().cloned().unwrap_or_default())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn pacing_spaces_calls_of_the_same_method() {
        let rpc = Rpc::new("http://127.0.0.1:9");
        let t0 = Instant::now();
        rpc.pace("getBlock").await;
        rpc.pace("getBlock").await;
        assert!(t0.elapsed() >= Duration::from_millis(1_050));
        let t1 = Instant::now();
        rpc.pace("getSlot").await;
        assert!(t1.elapsed() < Duration::from_millis(100));
    }
}

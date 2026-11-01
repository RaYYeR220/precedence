//! Two ways into a leader: direct QUIC (primary) and an RPC node's
//! `sendTransaction` (control). Both stamp every send with the run's clock.

pub mod quic;

use serde::Serialize;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct PathInfo {
    pub addr: String,
    pub handshake_us: Option<u64>,
    pub rtt_us: Option<u64>,
    /// An already-open connection to this address was reused.
    pub reused: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SendRecord {
    /// When the stream (or HTTP request) was opened.
    pub t_open_us: u64,
    /// When the last byte was handed to the transport.
    pub t_done_us: u64,
    pub err: Option<String>,
    pub dry: bool,
    /// The server had closed the connection; this send went out on a new one.
    pub reconnected: bool,
}

/// `sendTransaction` with `skipPreflight` and `maxRetries: 0`: one forward
/// from the RPC node, no rebroadcast, so a late landing is the path's own.
pub async fn send_via_rpc(rpc: &crate::rpc::Rpc, tb: &crate::clock::TimeBase, wire: &[u8], dry: bool) -> SendRecord {
    use base64::Engine;
    let t_open = tb.now_us();
    if dry {
        return SendRecord { t_open_us: t_open, t_done_us: t_open, err: None, dry: true, reconnected: false };
    }
    let b64 = base64::engine::general_purpose::STANDARD.encode(wire);
    let res = rpc.send_transaction(&b64).await;
    SendRecord { t_open_us: t_open, t_done_us: tb.now_us(), err: res.err().map(|e| e.to_string()), dry: false, reconnected: false }
}

/// Wait until `target` with sub-millisecond precision: sleep most of the
/// way, then spin. Timer wheels alone are too coarse for 0.5 ms spacing.
pub async fn wait_until(target: std::time::Instant) {
    let now = std::time::Instant::now();
    if target > now + std::time::Duration::from_millis(3) {
        tokio::time::sleep_until((target - std::time::Duration::from_millis(2)).into()).await;
    }
    while std::time::Instant::now() < target {
        std::hint::spin_loop();
    }
}

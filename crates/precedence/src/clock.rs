//! Slot clock: when does slot N start, as seen from here?
//!
//! We observe slots as they appear at an RPC node (WebSocket `slotsUpdates`
//! first-shred events, or plain `slotSubscribe`) and fit `t = a + b·slot`
//! over the recent past. A window's start is then predicted from the fit,
//! not from a fixed slot length: post-Alpenglow slots run ~213 ms, not 200.

use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio_tungstenite::tungstenite::Message;

/// A monotonic clock expressed in Unix microseconds: wall time sampled once
/// at start, advanced by `Instant`. Every timestamp of a run comes from here,
/// so send times and slot observations are directly comparable.
#[derive(Clone, Copy, Debug)]
pub struct TimeBase {
    start: Instant,
    start_unix_us: u64,
}

impl TimeBase {
    pub fn new() -> Self {
        let start = Instant::now();
        let start_unix_us = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_micros() as u64;
        TimeBase { start, start_unix_us }
    }
    pub fn now_us(&self) -> u64 {
        self.start_unix_us + self.start.elapsed().as_micros() as u64
    }
    pub fn instant_at(&self, unix_us: u64) -> Instant {
        if unix_us >= self.start_unix_us {
            self.start + Duration::from_micros(unix_us - self.start_unix_us)
        } else {
            self.start - Duration::from_micros(self.start_unix_us - unix_us)
        }
    }
}

impl Default for TimeBase {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq)]
pub struct Fit {
    /// Predicted unix µs of slot 0 (intercept).
    pub a: f64,
    /// Microseconds per slot.
    pub slot_us: f64,
    /// 95th percentile absolute residual of the fit, µs.
    pub resid_p95_us: f64,
    pub n: usize,
}

impl Fit {
    pub fn predict(&self, slot: u64) -> f64 {
        self.a + self.slot_us * slot as f64
    }
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct Observation {
    pub slot: u64,
    pub recv_unix_us: u64,
}

pub struct SlotClock {
    obs: VecDeque<Observation>,
    capacity: usize,
    last_recv_unix_us: Option<u64>,
    highest_slot: u64,
}

fn least_squares(pts: &[(f64, f64)]) -> Option<(f64, f64)> {
    let n = pts.len() as f64;
    if n < 2.0 {
        return None;
    }
    let mx = pts.iter().map(|p| p.0).sum::<f64>() / n;
    let my = pts.iter().map(|p| p.1).sum::<f64>() / n;
    let sxx: f64 = pts.iter().map(|p| (p.0 - mx).powi(2)).sum();
    if sxx == 0.0 {
        return None;
    }
    let sxy: f64 = pts.iter().map(|p| (p.0 - mx) * (p.1 - my)).sum();
    let b = sxy / sxx;
    Some((my - b * mx, b))
}

impl SlotClock {
    pub fn new(capacity: usize) -> Self {
        SlotClock { obs: VecDeque::with_capacity(capacity), capacity, last_recv_unix_us: None, highest_slot: 0 }
    }

    /// Record the first sighting of `slot`. Repeats and stale slots are ignored.
    pub fn observe(&mut self, slot: u64, recv_unix_us: u64) {
        self.last_recv_unix_us = Some(recv_unix_us);
        if slot <= self.highest_slot {
            return;
        }
        self.highest_slot = slot;
        if self.obs.len() == self.capacity {
            self.obs.pop_front();
        }
        self.obs.push_back(Observation { slot, recv_unix_us });
    }

    pub fn highest_slot(&self) -> u64 {
        self.highest_slot
    }

    /// No slot update for longer than `max_gap_us`.
    pub fn is_stale(&self, now_unix_us: u64, max_gap_us: u64) -> bool {
        self.last_recv_unix_us.is_none_or(|t| now_unix_us.saturating_sub(t) > max_gap_us)
    }

    /// Robust fit: least squares, drop points beyond 3·MAD, refit.
    /// Slots are re-based to the newest observation to keep precision.
    pub fn fit(&self) -> Option<Fit> {
        if self.obs.len() < 20 {
            return None;
        }
        let base = self.obs.back()?.slot as f64;
        let pts: Vec<(f64, f64)> = self.obs.iter().map(|o| (o.slot as f64 - base, o.recv_unix_us as f64)).collect();
        let (a0, b0) = least_squares(&pts)?;
        let mut res: Vec<f64> = pts.iter().map(|p| (p.1 - (a0 + b0 * p.0)).abs()).collect();
        let mut sorted = res.clone();
        sorted.sort_by(|x, y| x.partial_cmp(y).unwrap());
        let mad = sorted[sorted.len() / 2].max(1_000.0);
        let kept: Vec<(f64, f64)> = pts.iter().zip(&res).filter(|(_, r)| **r <= 3.0 * mad).map(|(p, _)| *p).collect();
        let (a, b) = least_squares(&kept)?;
        res = kept.iter().map(|p| (p.1 - (a + b * p.0)).abs()).collect();
        res.sort_by(|x, y| x.partial_cmp(y).unwrap());
        let p95 = res[((res.len() as f64 * 0.95) as usize).min(res.len() - 1)];
        Some(Fit { a: a - b * base, slot_us: b, resid_p95_us: p95, n: kept.len() })
    }
}

pub type SharedClock = Arc<Mutex<SlotClock>>;

/// Subscribe to slot updates and feed the clock until the task is dropped.
/// Prefers `slotsUpdatesSubscribe` (first shred received); falls back to
/// `slotSubscribe` if the endpoint refuses it. Reconnects on error.
pub async fn run_ws_feed(ws_url: String, clock: SharedClock, tb: TimeBase, log: Option<crate::io::JsonlWriter>) {
    let mut use_updates = true;
    loop {
        match tokio_tungstenite::connect_async(&ws_url).await {
            Ok((mut ws, _)) => {
                let method = if use_updates { "slotsUpdatesSubscribe" } else { "slotSubscribe" };
                let sub = serde_json::json!({"jsonrpc": "2.0", "id": 1, "method": method});
                if ws.send(Message::Text(sub.to_string().into())).await.is_err() {
                    continue;
                }
                while let Some(msg) = ws.next().await {
                    let Ok(Message::Text(txt)) = msg else {
                        if msg.is_err() {
                            break;
                        }
                        continue;
                    };
                    let now = tb.now_us();
                    let Ok(v) = serde_json::from_str::<serde_json::Value>(&txt) else { continue };
                    if v.get("error").is_some() && use_updates {
                        tracing::info!("slotsUpdatesSubscribe refused, falling back to slotSubscribe");
                        use_updates = false;
                        break;
                    }
                    let r = &v["params"]["result"];
                    let (slot, kind) = if use_updates {
                        (r["slot"].as_u64(), r["type"].as_str().unwrap_or(""))
                    } else {
                        (r["slot"].as_u64(), "slot")
                    };
                    let Some(slot) = slot else { continue };
                    // For slotsUpdates only the first-shred event marks a slot start.
                    if use_updates && kind != "firstShredReceived" {
                        continue;
                    }
                    clock.lock().unwrap().observe(slot, now);
                    if let Some(l) = &log {
                        l.write(&serde_json::json!({"slot": slot, "recv_unix_us": now, "source": "ws", "status": kind}));
                    }
                }
            }
            Err(e) => tracing::warn!(error = %e, "slot websocket connect failed"),
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn feed(clock: &mut SlotClock, start_slot: u64, n: u64, slot_us: f64, jitter_us: f64, seed: u64) -> f64 {
        use rand::{RngExt, SeedableRng};
        let mut rng = rand_chacha::ChaCha20Rng::seed_from_u64(seed);
        let t0 = 1_800_000_000_000_000f64;
        for i in 0..n {
            let j = (rng.random::<f64>() - 0.5) * 2.0 * jitter_us;
            clock.observe(start_slot + i, (t0 + slot_us * i as f64 + j) as u64);
        }
        t0
    }

    #[test]
    fn predicts_within_15ms_p95() {
        let mut c = SlotClock::new(200);
        let t0 = feed(&mut c, 1_000, 300, 213_000.0, 20_000.0, 1);
        let f = c.fit().unwrap();
        assert!((f.slot_us - 213_000.0).abs() < 300.0, "slot_us {}", f.slot_us);
        // predict 40 slots ahead (~8.5 s)
        let truth = t0 + 213_000.0 * 340.0;
        let err = (f.predict(1_340) - truth).abs();
        assert!(err < 15_000.0, "err {err}");
    }

    #[test]
    fn outliers_are_dropped() {
        let mut c = SlotClock::new(200);
        let t0 = feed(&mut c, 0, 150, 213_000.0, 5_000.0, 2);
        // a burst of late deliveries (network hiccup)
        for i in 150..160u64 {
            c.observe(i, (t0 + 213_000.0 * i as f64 + 900_000.0) as u64);
        }
        for i in 160..200u64 {
            c.observe(i, (t0 + 213_000.0 * i as f64) as u64);
        }
        let f = c.fit().unwrap();
        assert!((f.predict(250) - (t0 + 213_000.0 * 250.0)).abs() < 10_000.0);
    }

    #[test]
    fn stale_after_gap_and_ignores_repeats() {
        let mut c = SlotClock::new(50);
        c.observe(10, 1_000_000);
        c.observe(10, 1_100_000);
        c.observe(9, 1_200_000);
        assert_eq!(c.highest_slot(), 10);
        assert!(!c.is_stale(2_000_000, 2_000_000));
        assert!(c.is_stale(3_300_000, 2_000_000));
        assert!(SlotClock::new(5).is_stale(0, 1));
    }

    #[test]
    fn timebase_roundtrip() {
        let tb = TimeBase::new();
        let now = tb.now_us();
        let i = tb.instant_at(now + 5_000);
        let d = i.saturating_duration_since(Instant::now());
        assert!(d <= Duration::from_millis(6));
    }
}

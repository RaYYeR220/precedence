//! Slot clock: when does slot N start, as seen from here?
//!
//! We observe slots as they appear at an RPC node (WebSocket `slotsUpdates`
//! first-shred events, which carry the node's own timestamp, or plain
//! `slotSubscribe`). Post-Alpenglow slots are not evenly spaced: inside a
//! 4-slot leader window they start roughly 0 / 150 / 350 / 550 ms after the
//! window's first slot, and the boundary between windows waits for the
//! previous window's certificate. So the clock fits window starts against
//! window index and keeps the typical offset of each in-window position.

use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use std::collections::BTreeMap;
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
    /// Predicted unix µs of the start of window 0 (intercept).
    pub a: f64,
    /// Microseconds per 4-slot leader window.
    pub window_us: f64,
    /// Mean slot length (`window_us / 4`).
    pub slot_us: f64,
    /// Median start of in-window slot j relative to the window's first slot.
    pub in_window_offset_us: [f64; 4],
    /// 95th percentile absolute residual of the window-start fit, µs.
    pub resid_p95_us: f64,
    /// Windows the fit used.
    pub n: usize,
}

impl Fit {
    pub fn predict_window_start(&self, slot: u64) -> f64 {
        self.a + self.window_us * (slot / 4) as f64
    }
    /// Window start plus the slot's typical in-window offset.
    pub fn predict(&self, slot: u64) -> f64 {
        self.predict_window_start(slot) + self.in_window_offset_us[(slot % 4) as usize]
    }
}

/// Typical in-window slot starts as a fraction of the window, measured on
/// mainnet in November 2026 (0 / 147 / 347 / 546 ms of an ~852 ms window).
/// Used only until enough exact observations exist to measure them.
const DEFAULT_OFFSET_FRAC: [f64; 4] = [0.0, 0.173, 0.407, 0.641];
const MAX_KEEP: usize = 4_000;

pub struct SlotClock {
    /// slot -> the node's own first-shred time (or our receive time when the
    /// feed carries none).
    exact: BTreeMap<u64, u64>,
    /// slot -> midpoint of the first `getSlot` poll that returned it.
    poll: BTreeMap<u64, u64>,
    last_recv_unix_us: Option<u64>,
    highest_slot: u64,
}

fn median(mut v: Vec<f64>) -> Option<f64> {
    if v.is_empty() {
        return None;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    Some(v[v.len() / 2])
}

fn trim(m: &mut BTreeMap<u64, u64>) {
    while m.len() > MAX_KEEP {
        let k = *m.keys().next().unwrap();
        m.remove(&k);
    }
}

impl SlotClock {
    pub fn new() -> Self {
        SlotClock { exact: BTreeMap::new(), poll: BTreeMap::new(), last_recv_unix_us: None, highest_slot: 0 }
    }

    /// First sighting of `slot` on a push feed. `server_unix_us` is the
    /// node's own event time when the feed provides one.
    pub fn observe(&mut self, slot: u64, recv_unix_us: u64, server_unix_us: Option<u64>) {
        self.last_recv_unix_us = Some(recv_unix_us);
        self.highest_slot = self.highest_slot.max(slot);
        self.exact.entry(slot).or_insert(server_unix_us.unwrap_or(recv_unix_us));
        trim(&mut self.exact);
    }

    /// First time a `getSlot` poll returned `slot` (midpoint of the request).
    pub fn observe_poll(&mut self, slot: u64, mid_unix_us: u64) {
        self.last_recv_unix_us = Some(mid_unix_us);
        self.highest_slot = self.highest_slot.max(slot);
        self.poll.entry(slot).or_insert(mid_unix_us);
        trim(&mut self.poll);
    }

    pub fn highest_slot(&self) -> u64 {
        self.highest_slot
    }

    /// No slot update for longer than `max_gap_us`.
    pub fn is_stale(&self, now_unix_us: u64, max_gap_us: u64) -> bool {
        self.last_recv_unix_us.is_none_or(|t| now_unix_us.saturating_sub(t) > max_gap_us)
    }

    /// How much later a poll sees a slot than its exact first-shred time.
    pub fn poll_bias_us(&self) -> Option<f64> {
        let d: Vec<f64> = self
            .poll
            .iter()
            .rev()
            .filter_map(|(s, p)| self.exact.get(s).map(|e| *p as f64 - *e as f64))
            .take(300)
            .collect();
        (d.len() >= 10).then(|| median(d).unwrap())
    }

    fn in_window_offsets(&self) -> Option<[f64; 4]> {
        let mut offsets = [0.0f64; 4];
        for (j, off) in offsets.iter_mut().enumerate().skip(1) {
            let d: Vec<f64> = self
                .exact
                .iter()
                .filter(|(s, _)| *s % 4 == 0)
                .filter_map(|(s, t)| self.exact.get(&(s + j as u64)).map(|t2| *t2 as f64 - *t as f64))
                .filter(|d| *d > 0.0 && *d < 2_000_000.0)
                .collect();
            if d.len() < 6 {
                return None;
            }
            *off = median(d).unwrap();
        }
        Some(offsets)
    }

    /// Window starts behave like a random walk (each window waits for the
    /// previous one's certificate), so the prediction is anchored on the most
    /// recent window-start estimate and steps forward by the median recent
    /// window period. Every observed slot gives an estimate of its window's
    /// start (its time minus its typical in-window offset); exact
    /// observations win over bias-corrected polls. `resid_p95_us` is the
    /// historical one-step error of that rule.
    pub fn fit(&self) -> Option<Fit> {
        let offsets = self.in_window_offsets().unwrap_or_else(|| {
            let firsts: Vec<(u64, u64)> = self.exact.iter().filter(|(s, _)| *s % 4 == 0).map(|(s, t)| (*s, *t)).collect();
            let per: Vec<f64> = firsts
                .windows(2)
                .filter_map(|w| {
                    let k = (w[1].0 / 4).saturating_sub(w[0].0 / 4);
                    (1..=4).contains(&k).then(|| (w[1].1 as f64 - w[0].1 as f64) / k as f64)
                })
                .collect();
            let guess = median(per).unwrap_or(852_000.0);
            DEFAULT_OFFSET_FRAC.map(|f| f * guess)
        });
        let bias = self.poll_bias_us().unwrap_or(215_000.0);
        let mut by_window: BTreeMap<u64, (Vec<f64>, Vec<f64>)> = BTreeMap::new();
        for (s, t) in &self.exact {
            by_window.entry(s / 4).or_default().0.push(*t as f64 - offsets[(s % 4) as usize]);
        }
        for (s, t) in &self.poll {
            by_window.entry(s / 4).or_default().1.push(*t as f64 - bias - offsets[(s % 4) as usize]);
        }
        let pts: Vec<(u64, f64)> = by_window
            .into_iter()
            .map(|(k, (ex, po))| (k, if ex.is_empty() { median(po).unwrap() } else { median(ex).unwrap() }))
            .collect();
        if pts.len() < 8 {
            return None;
        }
        let periods: Vec<f64> = pts
            .windows(2)
            .filter_map(|w| {
                let k = w[1].0.checked_sub(w[0].0)?;
                (1..=4).contains(&k).then(|| (w[1].1 - w[0].1) / k as f64)
            })
            .filter(|p| *p > 300_000.0 && *p < 3_000_000.0)
            .collect();
        if periods.len() < 6 {
            return None;
        }
        let b = median(periods.iter().rev().take(24).copied().collect())?;
        let mut one_step: Vec<f64> = periods.iter().map(|p| (p - b).abs()).collect();
        one_step.sort_by(|x, y| x.partial_cmp(y).unwrap());
        let p95 = one_step[((one_step.len() as f64 * 0.95) as usize).min(one_step.len() - 1)];
        let (k_last, t_last) = *pts.last()?;
        Some(Fit {
            a: t_last - b * k_last as f64,
            window_us: b,
            slot_us: b / 4.0,
            in_window_offset_us: offsets,
            resid_p95_us: p95,
            n: pts.len(),
        })
    }
}

impl Default for SlotClock {
    fn default() -> Self {
        Self::new()
    }
}

pub type SharedClock = Arc<Mutex<SlotClock>>;

/// Subscribe to slot updates and feed the clock until the task is dropped.
/// Prefers `slotsUpdatesSubscribe` (first shred received, with the node's
/// timestamp); falls back to `slotSubscribe`. Reconnects on error.
pub async fn run_ws_feed(ws_url: String, clock: SharedClock, tb: TimeBase, log: Option<crate::io::JsonlWriter>) {
    let mut use_updates = true;
    // Public endpoints answer reconnect storms with HTTP 429: back off.
    let mut backoff = Duration::from_millis(500);
    loop {
        match tokio_tungstenite::connect_async(&ws_url).await {
            Ok((mut ws, _)) => {
                let method = if use_updates { "slotsUpdatesSubscribe" } else { "slotSubscribe" };
                let sub = serde_json::json!({"jsonrpc": "2.0", "id": 1, "method": method});
                if ws.send(Message::Text(sub.to_string().into())).await.is_err() {
                    continue;
                }
                backoff = Duration::from_millis(500);
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
                    let Some(slot) = r["slot"].as_u64() else { continue };
                    let kind = if use_updates { r["type"].as_str().unwrap_or("") } else { "slot" };
                    if use_updates && kind != "firstShredReceived" {
                        continue;
                    }
                    let server_us = r["timestamp"].as_u64().map(|ms| ms * 1_000);
                    clock.lock().unwrap().observe(slot, now, server_us);
                    if let Some(l) = &log {
                        l.write(&serde_json::json!({
                            "slot": slot, "recv_unix_us": now, "server_unix_us": server_us, "source": "ws", "status": kind
                        }));
                    }
                }
            }
            Err(e) => tracing::warn!(error = %e, backoff_ms = backoff.as_millis() as u64, "slot websocket connect failed"),
        }
        tokio::time::sleep(backoff).await;
        backoff = (backoff * 2).min(Duration::from_secs(30));
    }
}

/// Poll `getSlot` (processed) and feed the clock with the midpoint of each
/// request that returned a new slot. Complements the push feed, whose
/// delivery can lag by seconds even though its timestamps are exact.
pub async fn run_poll_feed(rpc_url: String, clock: SharedClock, tb: TimeBase, hz: f64, log: Option<crate::io::JsonlWriter>) {
    let http = reqwest::Client::builder().timeout(Duration::from_secs(2)).build().unwrap();
    let body = serde_json::json!({"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": [{"commitment": "processed"}]});
    let period = Duration::from_secs_f64(1.0 / hz);
    let mut last = 0u64;
    loop {
        let t0 = tb.now_us();
        if let Ok(r) = http.post(&rpc_url).json(&body).send().await {
            if let Ok(v) = r.json::<serde_json::Value>().await {
                let t1 = tb.now_us();
                if let Some(slot) = v["result"].as_u64() {
                    // Answers that took long cannot be placed in time precisely.
                    if slot > last && t1 - t0 < 250_000 {
                        last = slot;
                        let mid = (t0 + t1) / 2;
                        clock.lock().unwrap().observe_poll(slot, mid);
                        if let Some(l) = &log {
                            l.write(&serde_json::json!({
                                "slot": slot, "recv_unix_us": t1, "poll_mid_unix_us": mid, "rtt_us": t1 - t0, "source": "poll"
                            }));
                        }
                    }
                }
            }
        }
        let spent = Duration::from_micros(tb.now_us() - t0);
        tokio::time::sleep(period.saturating_sub(spent)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const T0: f64 = 1_800_000_000_000_000.0;
    const OFFS: [f64; 4] = [0.0, 147_000.0, 347_000.0, 546_000.0];

    /// Synthetic post-Alpenglow timeline with a jittery window boundary.
    fn timeline(windows: u64, window_us: f64, jitter_us: f64, seed: u64) -> Vec<(u64, f64)> {
        use rand::{RngExt, SeedableRng};
        let mut rng = rand_chacha::ChaCha20Rng::seed_from_u64(seed);
        let mut out = Vec::new();
        for w in 0..windows {
            let start = T0 + window_us * w as f64 + (rng.random::<f64>() - 0.5) * 2.0 * jitter_us;
            for (j, o) in OFFS.iter().enumerate() {
                out.push((1_000 + w * 4 + j as u64, start + o));
            }
        }
        out
    }

    #[test]
    fn learns_window_period_and_in_window_offsets() {
        let mut c = SlotClock::new();
        for &(s, t) in &timeline(60, 852_000.0, 40_000.0, 1) {
            // delivery is late, the node's timestamp is exact
            c.observe(s, t as u64 + 300_000, Some(t as u64));
        }
        let f = c.fit().unwrap();
        assert!((f.window_us - 852_000.0).abs() < 5_000.0, "window_us {}", f.window_us);
        assert!((f.in_window_offset_us[1] - 147_000.0).abs() < 1_000.0);
        assert!((f.in_window_offset_us[3] - 546_000.0).abs() < 1_000.0);
        let truth = T0 + 852_000.0 * 70.0;
        assert!((f.predict(1_000 + 70 * 4) - truth).abs() < 60_000.0);
    }

    #[test]
    fn outliers_are_dropped() {
        let mut c = SlotClock::new();
        for (i, &(s, t)) in timeline(60, 852_000.0, 5_000.0, 2).iter().enumerate() {
            let late = if (100..120).contains(&i) { 1_500_000.0 } else { 0.0 };
            c.observe(s, (t + late) as u64, None);
        }
        let f = c.fit().unwrap();
        assert!((f.predict_window_start(1_000 + 65 * 4) - (T0 + 852_000.0 * 65.0)).abs() < 15_000.0);
    }

    #[test]
    fn fresh_polls_fill_in_for_late_exact_feed() {
        let mut c = SlotClock::new();
        let tl = timeline(80, 852_000.0, 30_000.0, 4);
        // The exact feed has only delivered the first 50 windows; polls see
        // every slot 210 ms late but are current.
        for &(s, t) in tl.iter().take(50 * 4) {
            c.observe(s, t as u64 + 2_000_000, Some(t as u64));
        }
        for &(s, t) in &tl {
            c.observe_poll(s, t as u64 + 210_000);
        }
        let bias = c.poll_bias_us().unwrap();
        assert!((bias - 210_000.0).abs() < 1_000.0);
        let f = c.fit().unwrap();
        let (s_next, t_next) = (1_000 + 80 * 4, T0 + 852_000.0 * 80.0);
        assert!((f.predict(s_next) - t_next).abs() < 80_000.0, "err {}", f.predict(s_next) - t_next);
    }

    #[test]
    fn needs_enough_windows() {
        let mut c = SlotClock::new();
        for &(s, t) in &timeline(5, 852_000.0, 0.0, 3) {
            c.observe(s, t as u64, None);
        }
        assert!(c.fit().is_none());
    }

    #[test]
    fn stale_after_gap_and_ignores_repeats() {
        let mut c = SlotClock::new();
        c.observe(10, 1_000_000, None);
        c.observe(10, 1_100_000, None);
        c.observe(9, 1_200_000, None);
        assert_eq!(c.highest_slot(), 10);
        assert!(!c.is_stale(2_000_000, 2_000_000));
        assert!(c.is_stale(3_300_000, 2_000_000));
        assert!(SlotClock::new().is_stale(0, 1));
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

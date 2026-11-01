//! A measurement run: snapshot → plan → (pre-registered design) → one task
//! per batch that fires at its anchor → collect.

use crate::clock::{run_ws_feed, SharedClock, SlotClock, TimeBase};
use crate::cost;
use crate::design::PayerArm;
use crate::io::{write_json, JsonlWriter};
use crate::keys;
use crate::ledger::Ledger;
use crate::plan::{self, Path as SendPath, PlanParams, PlannedBatch};
use crate::rpc::Rpc;
use crate::send::{self, quic::QuicPool, SendRecord};
use crate::snapshot::{self, Snapshot};
use crate::targets::{pick_targets, Selection};
use crate::tx::{self, ProbeSpec};
use anyhow::{bail, Context, Result};
use serde::Serialize;
use serde_json::json;
use solana_hash::Hash;
use solana_keypair::Keypair;
use solana_signer::Signer;
use std::path::PathBuf;
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const LIVE_ENV: &str = "PRECEDENCE_LIVE";
pub const LIVE_VALUE: &str = "i-accept-spending-sol";

#[derive(Debug, Clone, Serialize)]
pub struct RunConfig {
    pub run_id: String,
    pub mode: String,
    pub live_refusal: Option<String>,
    pub run_seed: u64,
    pub rpc_url: String,
    pub ws_url: String,
    pub poll_url: String,
    pub poll_hz: f64,
    pub aux_rpc_url: String,
    pub grpc_url: Option<String>,
    pub cap_lamports: u64,
    pub cu_limit: u32,
    pub loaded_data_limit: u32,
    pub ladder8: Vec<u64>,
    pub ladder12: Vec<u64>,
    pub tie_fee: u64,
    pub format_levels: Vec<u64>,
    pub families: Vec<String>,
    pub per_family: usize,
    pub batches_per_target: usize,
    pub horizon_slots: u64,
    pub lead_slots: u64,
    pub payer_pool: u16,
    pub pilot: bool,
    pub git_commit: String,
    pub started_unix_us: u64,
    pub ended_unix_us: Option<u64>,
    pub master: String,
}

#[derive(Debug, Clone)]
pub struct RunArgs {
    pub data_dir: PathBuf,
    pub live_flag: bool,
    pub keypair: PathBuf,
    pub rpc_url: String,
    pub ws_url: String,
    pub poll_url: String,
    pub poll_hz: f64,
    /// Second RPC for blockhashes and block collection, so the public
    /// endpoint's per-IP limits never sit on a batch's critical path.
    pub aux_rpc_url: String,
    pub grpc_url: Option<String>,
    pub grpc_token: Option<String>,
    pub cap_lamports: u64,
    pub seed: Option<u64>,
    pub families: Vec<String>,
    pub per_family: usize,
    pub batches_per_target: usize,
    pub horizon_slots: u64,
    pub lead_slots: u64,
    pub payer_pool: u16,
    pub cu_limit: u32,
    pub loaded_data_limit: u32,
    pub pilot: bool,
    pub min_stake_sol: u64,
    pub collect_wait_s: u64,
}

/// Live needs all three: the flag, the environment acknowledgement and a
/// keypair file. Anything missing means a dry run, with the reason recorded.
pub fn live_gate(flag: bool, env: Option<&str>, keypair_exists: bool) -> std::result::Result<(), String> {
    let mut missing = Vec::new();
    if !flag {
        missing.push("--live flag not given".to_string());
    }
    if env != Some(LIVE_VALUE) {
        missing.push(format!("{LIVE_ENV} is not set to '{LIVE_VALUE}'"));
    }
    if !keypair_exists {
        missing.push("no keypair file mounted".to_string());
    }
    if missing.is_empty() {
        Ok(())
    } else {
        Err(missing.join("; "))
    }
}

pub fn git_commit() -> String {
    option_env!("PRECEDENCE_GIT_COMMIT").unwrap_or("unknown").to_string()
}

fn new_run_id(tb: &TimeBase) -> String {
    let now = chrono::DateTime::from_timestamp_micros(tb.now_us() as i64).unwrap();
    format!("{}-{:06x}", now.format("%Y%m%dT%H%MZ"), rand::random::<u32>() & 0xffffff)
}

struct Ctx {
    /// Blockhashes (auxiliary endpoint).
    rpc: Rpc,
    /// The RPC path's `sendTransaction` goes to the public mainnet endpoint, never
    /// to a provider that reroutes transactions through private relays.
    send_rpc: Rpc,
    tb: TimeBase,
    clock: SharedClock,
    payers: Vec<Keypair>,
    ledger: Option<Mutex<Ledger>>,
    probes: JsonlWriter,
    windows: JsonlWriter,
    log: JsonlWriter,
    dry: bool,
    run_id: String,
    cu_limit: u32,
    loaded_data_limit: u32,
    cap_hit: AtomicBool,
    quic: QuicPool,
    /// Latest blockhash and its slot, refreshed in the background so that
    /// fetching one is never on a batch's critical path.
    blockhash: Mutex<Option<(String, u64)>>,
}

/// A blockhash older than this (slots behind our clock's tip, which comes
/// from first-shred events and is current) is refused: it must stay valid
/// (150 blocks) through the send and any late landing. Free RPC nodes replay
/// behind the tip, and some fall further behind over a run, so the freshest
/// of several sources is used.
const MAX_BLOCKHASH_LAG_SLOTS: u64 = 80;

/// A send may start this late and still go out; the analysis uses the
/// measured send time, so a small delay only shifts the batch's phase.
const MAX_LATE_US: u64 = 150_000;

#[derive(Serialize)]
struct WindowRecord<'a> {
    batch_id: u32,
    target: &'a str,
    family: &'a str,
    version: &'a str,
    window_first_slot: u64,
    tpu_quic: &'a str,
    tpu_is_proxy: bool,
    path: SendPath,
    kind: crate::design::ProbeKind,
    arm: PayerArm,
    slot_us: Option<f64>,
    clock_resid_p95_us: Option<f64>,
    predicted_window_start_unix_us: Option<u64>,
    first_send_unix_us: Option<u64>,
    highest_slot_seen_at_send: Option<u64>,
    rtt_us: Option<u64>,
    handshake_us: Option<u64>,
    blockhash: Option<String>,
    blockhash_slot: Option<u64>,
    status: String,
}

impl Ctx {
    fn skip(&self, b: &PlannedBatch, reason: &str, extra: impl FnOnce(&mut WindowRecord)) {
        let mut w = WindowRecord {
            batch_id: b.batch_id,
            target: &b.target,
            family: &b.family,
            version: &b.version,
            window_first_slot: b.window_first_slot,
            tpu_quic: &b.tpu_quic,
            tpu_is_proxy: b.tpu_is_proxy,
            path: b.path,
            kind: b.design.kind,
            arm: b.design.arm,
            slot_us: None,
            clock_resid_p95_us: None,
            predicted_window_start_unix_us: None,
            first_send_unix_us: None,
            highest_slot_seen_at_send: None,
            rtt_us: None,
            handshake_us: None,
            blockhash: None,
            blockhash_slot: None,
            status: format!("skipped:{reason}"),
        };
        extra(&mut w);
        self.windows.write(&w);
        tracing::info!(batch = b.batch_id, reason, "batch skipped");
    }
}

/// Prepare and fire one batch.
async fn execute_batch(ctx: Arc<Ctx>, b: PlannedBatch) {
    if let Err(e) = execute_batch_inner(&ctx, &b).await {
        ctx.skip(&b, "error", |_| {});
        ctx.log.write(&json!({"batch_id": b.batch_id, "error": e.to_string()}));
        tracing::warn!(batch = b.batch_id, error = %e, "batch failed");
    }
}

fn first_send_unix_us(ctx: &Ctx, b: &PlannedBatch) -> Option<(u64, u64, crate::clock::Fit)> {
    let fit = ctx.clock.lock().unwrap().fit()?;
    let start = fit.predict_window_start(b.window_first_slot);
    let first = start + b.design.anchor.offset_us(fit.slot_us) as f64;
    Some((start as u64, first as u64, fit))
}

async fn execute_batch_inner(ctx: &Ctx, b: &PlannedBatch) -> Result<()> {
    let Some((_, first, _)) = first_send_unix_us(ctx, b) else {
        ctx.skip(b, "no_clock", |_| {});
        return Ok(());
    };
    // Start preparing 1.2 s ahead: take the cached blockhash, sign, make sure
    // the pooled connection is alive. Idle unstaked connections get pruned,
    // so we do not open them earlier than needed.
    let prepare_at = first.saturating_sub(1_200_000);
    let now = ctx.tb.now_us();
    if now + 400_000 > first {
        ctx.skip(b, "late_before_prepare", |_| {});
        ctx.log.write(&json!({"batch_id": b.batch_id, "late_before_prepare_us": (now + 400_000).saturating_sub(first)}));
        return Ok(());
    }
    if prepare_at > now {
        tokio::time::sleep_until(ctx.tb.instant_at(prepare_at).into()).await;
    }
    if ctx.cap_hit.load(Ordering::SeqCst) {
        ctx.skip(b, "cap", |_| {});
        return Ok(());
    }
    let mut quic = None;
    let mut path_info = None;
    if b.path == SendPath::Quic {
        let addr: std::net::SocketAddr = b.tpu_quic.parse().context("tpu address")?;
        match ctx.quic.connection(addr).await {
            Ok((conn, info)) => {
                path_info = Some(info);
                quic = Some((addr, conn));
            }
            Err(e) => {
                ctx.skip(b, "connect", |_| {});
                ctx.log.write(&json!({"batch_id": b.batch_id, "connect_error": e.to_string()}));
                return Ok(());
            }
        }
    }
    let cached = ctx.blockhash.lock().unwrap().clone();
    let tip = ctx.clock.lock().unwrap().highest_slot();
    let (bh, bh_slot) = match cached {
        Some(c) if tip <= c.1 + MAX_BLOCKHASH_LAG_SLOTS / 2 => c,
        _ => {
            // Ask both HTTP endpoints now; the freshest answer wins.
            let (a, b2) = tokio::join!(ctx.send_rpc.latest_blockhash(), ctx.rpc.latest_blockhash());
            [cached, a.ok(), b2.ok()].into_iter().flatten().max_by_key(|c| c.1).context("no blockhash source answered")?
        }
    };
    let highest = ctx.clock.lock().unwrap().highest_slot();
    if highest > bh_slot + MAX_BLOCKHASH_LAG_SLOTS {
        ctx.skip(b, "stale_blockhash", |_| {});
        ctx.log.write(&json!({"batch_id": b.batch_id, "stale_blockhash_lag_slots": highest - bh_slot}));
        return Ok(());
    }
    let blockhash = Hash::from_str(&bh).context("blockhash")?;
    let sink = (b.design.arm == PayerArm::Shared).then(|| keys::shared_sink(&ctx.run_id, b.batch_id));
    let run_num = u32::from_str_radix(&ctx.run_id[ctx.run_id.len() - 6..], 16).unwrap_or(0);
    let mut built = Vec::with_capacity(b.design.probes.len());
    for p in &b.design.probes {
        let payer = &ctx.payers[p.payer_slot as usize];
        let spec = ProbeSpec {
            payer: payer.pubkey(),
            priority_fee: p.priority_fee,
            cu_limit: ctx.cu_limit,
            loaded_data_limit: ctx.loaded_data_limit,
            tag: tx::make_tag(run_num, b.batch_id, p.idx),
            format: p.format,
            shared_write: sink,
        };
        let t = tx::build_probe(&spec, blockhash.clone(), payer)?;
        let est_cost = cost::estimated_cost(&t);
        built.push((p.clone(), t.signatures[0].to_string(), tx::to_wire(&t), est_cost, spec));
    }
    if let Some(ledger) = &ctx.ledger {
        let items: Vec<(String, u64)> =
            built.iter().map(|(p, sig, ..)| (sig.clone(), cost::BASE_FEE_PER_SIGNATURE + p.priority_fee)).collect();
        if let Err(e) = ledger.lock().unwrap().reserve("probe", Some(b.batch_id), &items) {
            ctx.cap_hit.store(true, Ordering::SeqCst);
            ctx.skip(b, "cap", |_| {});
            ctx.log.write(&json!({"batch_id": b.batch_id, "cap": e.to_string()}));
            return Ok(());
        }
    }
    // Re-predict from the freshest fit right before sending.
    let (stale, fit_now) = {
        let c = ctx.clock.lock().unwrap();
        (c.is_stale(ctx.tb.now_us(), 2_000_000), c.fit())
    };
    let Some(fit) = fit_now.filter(|_| !stale) else {
        ctx.skip(b, "clock_stale", |_| {});
        return Ok(());
    };
    let start = fit.predict_window_start(b.window_first_slot);
    let first = (start + b.design.anchor.offset_us(fit.slot_us) as f64) as u64;
    let now = ctx.tb.now_us();
    if now > first + MAX_LATE_US {
        ctx.skip(b, "late_at_send", |_| {});
        ctx.log.write(&json!({"batch_id": b.batch_id, "late_at_send_us": now - first}));
        return Ok(());
    }
    // Slightly late: start now, keep the planned spacing.
    let first = first.max(now);
    // Send in send order.
    let mut order: Vec<usize> = (0..built.len()).collect();
    order.sort_by_key(|&i| built[i].0.send_rank);
    let mut records: Vec<Option<SendRecord>> = vec![None; built.len()];
    match b.path {
        SendPath::Quic => {
            let (addr, conn) = quic.as_mut().unwrap();
            // The server may have pruned the connection while we prepared.
            if conn.close_reason().is_some() {
                if let Ok((fresh, _)) = ctx.quic.connection(*addr).await {
                    *conn = fresh;
                }
            }
            for &i in &order {
                let at = first as i64 + built[i].0.send_offset_us;
                send::wait_until(ctx.tb.instant_at(at as u64)).await;
                records[i] = Some(ctx.quic.send(*addr, conn, &built[i].2).await);
            }
        }
        SendPath::Rpc => {
            let mut handles = Vec::new();
            for &i in &order {
                let at = first as i64 + built[i].0.send_offset_us;
                send::wait_until(ctx.tb.instant_at(at as u64)).await;
                let (rpc, tb, wire, dry) = (ctx.send_rpc.clone(), ctx.tb, built[i].2.clone(), ctx.dry);
                handles.push((i, tokio::spawn(async move { send::send_via_rpc(&rpc, &tb, &wire, dry).await })));
            }
            for (i, h) in handles {
                records[i] = Some(h.await.unwrap_or_else(|e| SendRecord {
                    t_open_us: 0,
                    t_done_us: 0,
                    err: Some(e.to_string()),
                    dry: ctx.dry,
                    reconnected: false,
                }));
            }
        }
    }
    let highest_at_send = ctx.clock.lock().unwrap().highest_slot();
    for (i, (p, sig, wire, est_cost, spec)) in built.iter().enumerate() {
        let r = records[i].as_ref().unwrap();
        ctx.probes.write(&json!({
            "batch_id": b.batch_id,
            "idx": p.idx,
            "signature": sig,
            "payer": spec.payer.to_string(),
            "shared_write": spec.shared_write.map(|a| a.to_string()),
            "priority_fee": p.priority_fee,
            "format": p.format,
            "est_cost": est_cost,
            "agave_priority": cost::agave_priority(p.priority_fee, 1, *est_cost),
            "send_rank": p.send_rank,
            "planned_offset_us": p.send_offset_us,
            "sent_unix_us": if r.dry { None } else { Some(r.t_open_us) },
            "sent_done_unix_us": if r.dry { None } else { Some(r.t_done_us) },
            "dry_send_unix_us": if r.dry { Some(r.t_open_us) } else { None },
            "send_err": r.err,
            "reconnected": r.reconnected,
            "wire_len": wire.len(),
        }));
    }
    let rtt_now = quic.as_ref().map(|(_, c)| c.rtt().as_micros() as u64);
    ctx.windows.write(&WindowRecord {
        batch_id: b.batch_id,
        target: &b.target,
        family: &b.family,
        version: &b.version,
        window_first_slot: b.window_first_slot,
        tpu_quic: &b.tpu_quic,
        tpu_is_proxy: b.tpu_is_proxy,
        path: b.path,
        kind: b.design.kind,
        arm: b.design.arm,
        slot_us: Some(fit.slot_us),
        clock_resid_p95_us: Some(fit.resid_p95_us),
        predicted_window_start_unix_us: Some(start as u64),
        first_send_unix_us: Some(first),
        highest_slot_seen_at_send: Some(highest_at_send),
        rtt_us: rtt_now.or(path_info.as_ref().and_then(|p| p.rtt_us)),
        handshake_us: path_info.as_ref().and_then(|p| p.handshake_us),
        blockhash: Some(bh),
        blockhash_slot: Some(bh_slot),
        status: if ctx.dry { "dry".into() } else { "sent".into() },
    });
    // The pooled connection stays open for the next batch to the same TPU.
    Ok(())
}

pub async fn run(args: RunArgs) -> Result<PathBuf> {
    let tb = TimeBase::new();
    let gate = live_gate(args.live_flag, std::env::var(LIVE_ENV).ok().as_deref(), args.keypair.exists());
    let dry = gate.is_err();
    let master = if args.keypair.exists() { keys::load_keypair(&args.keypair)? } else { keys::ephemeral_master() };
    let run_id = new_run_id(&tb);
    let run_dir = args.data_dir.join("runs").join(&run_id);
    std::fs::create_dir_all(&run_dir)?;
    let run_seed = args.seed.unwrap_or_else(rand::random);
    match &gate {
        Ok(()) => tracing::warn!(%run_id, "LIVE run: transactions will be sent and fees spent"),
        Err(why) => tracing::info!(%run_id, %why, "dry run: nothing will be sent"),
    }
    let rpc = Rpc::new(&args.rpc_url);
    let aux = Rpc::new(&args.aux_rpc_url).with_spacing("getBlock", Duration::from_millis(150));
    let snap: Snapshot = snapshot::take_snapshot(&rpc, &run_dir.join("snapshot")).await?;
    if let Err(e) = snapshot::check_regime(&snap.features) {
        if !dry {
            bail!(e);
        }
        tracing::warn!(error = %e, "regime check failed (dry run continues)");
    }
    let mut cfg = RunConfig {
        run_id: run_id.clone(),
        mode: if dry { "dry".into() } else { "live".into() },
        live_refusal: gate.clone().err(),
        run_seed,
        rpc_url: args.rpc_url.clone(),
        ws_url: args.ws_url.clone(),
        poll_url: args.poll_url.clone(),
        poll_hz: args.poll_hz,
        aux_rpc_url: args.aux_rpc_url.clone(),
        grpc_url: args.grpc_url.clone(),
        cap_lamports: args.cap_lamports,
        cu_limit: args.cu_limit,
        loaded_data_limit: args.loaded_data_limit,
        ladder8: crate::design::LADDER8.to_vec(),
        ladder12: crate::design::LADDER12.to_vec(),
        tie_fee: crate::design::TIE_FEE,
        format_levels: crate::design::FORMAT_LEVELS.to_vec(),
        families: args.families.clone(),
        per_family: args.per_family,
        batches_per_target: args.batches_per_target,
        horizon_slots: args.horizon_slots,
        lead_slots: args.lead_slots,
        payer_pool: args.payer_pool,
        pilot: args.pilot,
        git_commit: git_commit(),
        started_unix_us: tb.now_us(),
        ended_unix_us: None,
        master: master.pubkey().to_string(),
    };
    write_json(run_dir.join("config.json"), &cfg)?;

    // Slot clock.
    let clock: SharedClock = Arc::new(Mutex::new(SlotClock::new()));
    let slots_log = JsonlWriter::open(run_dir.join("slots.jsonl"))?;
    let feed = tokio::spawn(run_ws_feed(args.ws_url.clone(), clock.clone(), tb, Some(slots_log.clone())));
    // With an entry stream the clock has exact, current first-shred times;
    // polling would only add load on the public endpoint.
    let poller = args.grpc_url.is_none().then(|| {
        tokio::spawn(crate::clock::run_poll_feed(args.poll_url.clone(), clock.clone(), tb, args.poll_hz, Some(slots_log)))
    });
    // The entry stream starts now: its first-shred events (with the provider's
    // timestamps) are the most current slot clock we have.
    let wanted: crate::yellowstone::Wanted = Default::default();
    let recorder = args.grpc_url.as_ref().map(|url| {
        tokio::spawn(crate::yellowstone::record(
            url.clone(),
            args.grpc_token.clone(),
            wanted.clone(),
            run_dir.clone(),
            tb,
            Some(clock.clone()),
        ))
    });
    for _ in 0..120 {
        if clock.lock().unwrap().fit().is_some() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
    let fit = clock.lock().unwrap().fit().context("slot clock never converged (websocket?)")?;
    let now_slot = clock.lock().unwrap().highest_slot();
    tracing::info!(slot_us = fit.slot_us, resid_p95_us = fit.resid_p95_us, now_slot, "slot clock ready");

    // Targets and plan.
    let sel = Selection {
        families: args.families.clone(),
        per_family: args.per_family,
        min_stake_lamports: args.min_stake_sol * crate::ledger::LAMPORTS_PER_SOL,
        min_windows: 2,
        from_slot: now_slot + args.lead_slots,
        to_slot: now_slot + args.horizon_slots,
    };
    let targets = pick_targets(&snap, &sel);
    write_json(run_dir.join("targets.json"), &targets)?;
    let params = PlanParams {
        run_seed,
        cycle: if args.pilot { plan::PILOT_CYCLE.to_vec() } else { plan::MAIN_CYCLE.to_vec() },
        batches_per_target: args.batches_per_target,
        min_slot: now_slot + args.lead_slots,
        min_gap_slots: 8,
        payer_pool: args.payer_pool,
    };
    let batches = plan::plan(&targets, &params);
    let hash = plan::plan_hash(&batches);
    write_json(
        run_dir.join("design.json"),
        &json!({"run_id": run_id, "run_seed": run_seed, "sha256": hash, "batches": batches}),
    )?;
    tracing::info!(batches = batches.len(), targets = targets.len(), design_sha256 = %hash, "design written before first send");
    if batches.is_empty() {
        bail!("no batches planned: no target has windows in range");
    }

    let ledger = if dry {
        None
    } else {
        Some(Mutex::new(Ledger::open(args.data_dir.join("ledger").join("spend.jsonl"), args.cap_lamports, &run_id)?))
    };
    let ctx = Arc::new(Ctx {
        rpc: aux.clone(),
        send_rpc: rpc.clone(),
        tb,
        clock: clock.clone(),
        payers: keys::derive_payers(&master, args.payer_pool),
        ledger,
        probes: JsonlWriter::open(run_dir.join("probes.jsonl"))?,
        windows: JsonlWriter::open(run_dir.join("windows.jsonl"))?,
        log: JsonlWriter::open(run_dir.join("log.jsonl"))?,
        dry,
        run_id: run_id.clone(),
        cu_limit: args.cu_limit,
        loaded_data_limit: args.loaded_data_limit,
        cap_hit: AtomicBool::new(false),
        quic: QuicPool::new(tb, dry)?,
        blockhash: Mutex::new(None),
    });
    let bh_refresh = {
        let ctx = ctx.clone();
        let mut grpc = args.grpc_url.clone().map(|u| crate::yellowstone::BlockhashSource::new(u, args.grpc_token.clone()));
        tokio::spawn(async move {
            loop {
                let grpc_fut = async {
                    match grpc.as_mut() {
                        Some(g) => g.latest().await.ok(),
                        None => None,
                    }
                };
                let (g, a, x) = tokio::join!(grpc_fut, ctx.send_rpc.latest_blockhash(), ctx.rpc.latest_blockhash());
                let best = [g, a.ok(), x.ok()].into_iter().flatten().max_by_key(|c| c.1);
                match best {
                    Some(v) => {
                        let mut slot = ctx.blockhash.lock().unwrap();
                        if slot.as_ref().is_none_or(|c| v.1 >= c.1) {
                            *slot = Some(v);
                        }
                    }
                    None => tracing::warn!("every blockhash source failed"),
                }
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
        })
    };

    // Record entries for the planned windows and their neighbours (probes can
    // spill into the next leader's block).
    wanted
        .lock()
        .unwrap()
        .extend(batches.iter().flat_map(|b| b.window_first_slot.saturating_sub(8)..(b.window_first_slot + 24)));

    // Periodic gossip refresh (clientId can flip with BAM connection state).
    let refresh = {
        let (rpc, dir) = (rpc.clone(), run_dir.join("snapshot"));
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(600)).await;
                if let Err(e) = snapshot::refresh_nodes(&rpc, &dir).await {
                    tracing::warn!(error = %e, "gossip refresh failed");
                }
            }
        })
    };

    let last_slot = batches.iter().map(|b| b.window_first_slot).max().unwrap();
    let mut handles = Vec::new();
    for b in batches {
        handles.push(tokio::spawn(execute_batch(ctx.clone(), b)));
    }
    for h in handles {
        let _ = h.await;
    }
    tracing::info!(last_slot, "all batches done; waiting for finalization");
    refresh.abort();
    bh_refresh.abort();
    let _ = snapshot::refresh_nodes(&rpc, &run_dir.join("snapshot")).await;
    tokio::time::sleep(Duration::from_secs(args.collect_wait_s)).await;
    if let Some(r) = recorder {
        r.abort();
    }
    feed.abort();
    if let Some(p) = poller {
        p.abort();
    }
    if !dry {
        let summary = crate::collect::collect(&aux, &args.data_dir, &run_dir).await?;
        write_json(run_dir.join("collect.json"), &summary)?;
        settle(&ctx, &run_dir).await?;
        // Batches skipped after their reservation never left the process:
        // close those reservations from the chain's answer.
        if let Some(l) = &ctx.ledger {
            let path = args.data_dir.join("ledger").join("spend.jsonl");
            let mut guard = l.lock().unwrap();
            let fut = crate::reconcile::reconcile(&aux, &mut guard, &path);
            let r = tokio::task::block_in_place(|| tokio::runtime::Handle::current().block_on(fut))?;
            write_json(run_dir.join("reconcile.json"), &r)?;
        }
    }
    cfg.ended_unix_us = Some(tb.now_us());
    write_json(run_dir.join("config.json"), &cfg)?;
    if let Some(l) = &ctx.ledger {
        let t = l.lock().unwrap().totals();
        tracing::info!(reserved = t.reserved, settled = t.settled, cap = t.cap, "spend ledger");
    }
    Ok(run_dir)
}

/// Record the fee actually charged for every probe (0 for the ones that
/// never landed) so the ledger shows reserved vs spent.
async fn settle(ctx: &Ctx, run_dir: &std::path::Path) -> Result<()> {
    let Some(ledger) = &ctx.ledger else { return Ok(()) };
    let statuses: serde_json::Value = serde_json::from_slice(&std::fs::read(run_dir.join("statuses.json"))?)?;
    let probes = crate::io::read_jsonl(run_dir.join("probes.jsonl"))?;
    let mut l = ledger.lock().unwrap();
    for p in probes {
        let Some(sig) = p["signature"].as_str() else { continue };
        if p["sent_unix_us"].is_null() {
            continue;
        }
        let landed = statuses.get(sig).map(|s| !s.is_null()).unwrap_or(false);
        let fee = if landed { cost::BASE_FEE_PER_SIGNATURE + p["priority_fee"].as_u64().unwrap_or(0) } else { 0 };
        l.settle("probe", sig, fee)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn live_needs_all_three_gates() {
        assert!(live_gate(true, Some(LIVE_VALUE), true).is_ok());
        let e = live_gate(false, Some(LIVE_VALUE), true).unwrap_err();
        assert!(e.contains("--live"));
        let e = live_gate(true, Some("yes"), true).unwrap_err();
        assert!(e.contains(LIVE_ENV));
        let e = live_gate(true, Some(LIVE_VALUE), false).unwrap_err();
        assert!(e.contains("keypair"));
        let e = live_gate(false, None, false).unwrap_err();
        assert_eq!(e.matches(';').count(), 2);
    }
}

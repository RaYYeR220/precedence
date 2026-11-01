use anyhow::{Context, Result};
use clap::{Args, Parser, Subcommand};
use precedence::{collect, fund, io, keys, ledger, rpc::Rpc, run, verify, yellowstone};
use serde_json::json;
use solana_signer::Signer;
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "precedence", version, about = "Measure how Solana mainnet leaders order transactions inside their blocks")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Args, Clone)]
struct Common {
    /// Data directory (runs/, blocks/, ledger/).
    #[arg(long, env = "PRECEDENCE_DATA", default_value = "data")]
    data: PathBuf,
    #[arg(long, env = "PRECEDENCE_RPC", default_value = "https://api.mainnet-beta.solana.com")]
    rpc: String,
    /// Operator keypair (Solana CLI JSON). Mounted read-only; never in the repo.
    #[arg(long, env = "PRECEDENCE_KEYPAIR", default_value = "/keys/burner.json")]
    keypair: PathBuf,
    /// Spend cap in lamports (default 0.05 SOL, hard maximum 0.06 SOL).
    #[arg(long, env = "PRECEDENCE_CAP_LAMPORTS", default_value_t = ledger::DEFAULT_CAP)]
    cap: u64,
    /// Actually send. Also requires PRECEDENCE_LIVE and a mounted keypair.
    #[arg(long)]
    live: bool,
}

#[derive(Subcommand)]
enum Cmd {
    /// Snapshot, plan, fire every batch at its window, collect (dry-run unless --live).
    Run {
        #[command(flatten)]
        common: Common,
        #[arg(long, env = "PRECEDENCE_WS", default_value = "wss://api.mainnet-beta.solana.com")]
        ws: String,
        /// HTTP endpoint polled with getSlot for a fresh slot clock.
        #[arg(long, env = "PRECEDENCE_POLL_RPC", default_value = "https://api.mainnet-beta.solana.com")]
        poll_rpc: String,
        #[arg(long, default_value_t = 3.5)]
        poll_hz: f64,
        /// RPC for blockhashes and block collection (needs v1 getBlock and recent history).
        #[arg(long, env = "PRECEDENCE_AUX_RPC", default_value = "https://solana.leorpc.com/?api_key=FREE")]
        aux_rpc: String,
        /// Yellowstone gRPC endpoint for entry boundaries (optional).
        #[arg(long, env = "PRECEDENCE_GRPC")]
        grpc: Option<String>,
        #[arg(long, env = "PRECEDENCE_GRPC_TOKEN", hide_env_values = true)]
        grpc_token: Option<String>,
        #[arg(long, env = "PRECEDENCE_SEED")]
        seed: Option<u64>,
        #[arg(
            long,
            value_delimiter = ',',
            default_value = "AgaveBam,JitoLabs,HarmonicAgave,HarmonicFiredancer,Firedancer,FireBAM,Rakurai,Agave"
        )]
        families: Vec<String>,
        #[arg(long, default_value_t = 2)]
        per_family: usize,
        #[arg(long, default_value_t = 11)]
        batches_per_target: usize,
        /// How far ahead to plan, in slots (~213 ms each).
        #[arg(long, default_value_t = 9_000)]
        horizon_slots: u64,
        #[arg(long, default_value_t = 30)]
        lead_slots: u64,
        #[arg(long, default_value_t = 16)]
        payer_pool: u16,
        #[arg(long, env = "PRECEDENCE_CU_LIMIT", default_value_t = 6_500)]
        cu_limit: u32,
        #[arg(long, env = "PRECEDENCE_LOADED_LIMIT", default_value_t = 81_920)]
        loaded_data_limit: u32,
        #[arg(long, default_value_t = 50_000)]
        min_stake_sol: u64,
        #[arg(long, default_value_t = 30)]
        collect_wait_s: u64,
        /// One batch of each kind per target: landing and timing check.
        #[arg(long)]
        pilot: bool,
    },
    /// (Re)collect statuses and blocks for one run.
    Collect {
        #[command(flatten)]
        common: Common,
        run_dir: PathBuf,
    },
    /// Re-check every stored probe position against getBlock.
    Verify {
        #[command(flatten)]
        common: Common,
        #[arg(long, default_value = "results/verify.json")]
        out: PathBuf,
    },
    /// Top up the payer pool from the operator key.
    Fund {
        #[command(flatten)]
        common: Common,
        #[arg(long, default_value_t = 16)]
        payers: u16,
        #[arg(long, default_value_t = 3_000_000)]
        lamports_each: u64,
    },
    /// Return every payer's balance to the operator key.
    Sweep {
        #[command(flatten)]
        common: Common,
        #[arg(long, default_value_t = 16)]
        payers: u16,
    },
    /// Settle every open reservation from the chain (actual fee, or zero if it never landed).
    Reconcile {
        #[command(flatten)]
        common: Common,
    },
    /// Balances and spend-ledger totals.
    Status {
        #[command(flatten)]
        common: Common,
        #[arg(long, default_value_t = 16)]
        payers: u16,
    },
    /// Simulate one probe of each shape to measure its compute and loaded data.
    Calibrate {
        #[command(flatten)]
        common: Common,
    },
    /// Positive control against a local Agave validator.
    Selftest {
        #[arg(long, default_value = "http://127.0.0.1:8899")]
        rpc: String,
        #[arg(long, default_value = "data-localnet")]
        data: PathBuf,
        #[arg(long, default_value_t = 8)]
        reps: usize,
        #[arg(long, default_value_t = 1)]
        seed: u64,
        #[arg(long, default_value_t = 8_000)]
        cu_limit: u32,
        #[arg(long, default_value_t = 262_144)]
        loaded_data_limit: u32,
    },
    /// After a selftest: dump entry boundaries of the probe slots from the local ledger.
    LocalnetEntries {
        #[arg(long)]
        ledger: PathBuf,
        #[arg(long)]
        run: PathBuf,
        #[arg(long, default_value = "agave-ledger-tool")]
        tool: String,
    },
    /// Subscribe to the entry stream for a few seconds and print what arrives.
    EntriesProbe {
        #[arg(long, env = "PRECEDENCE_GRPC")]
        grpc: String,
        #[arg(long, env = "PRECEDENCE_GRPC_TOKEN", hide_env_values = true)]
        grpc_token: Option<String>,
        #[arg(long, default_value_t = 5)]
        seconds: u64,
    },
}

fn live_or_dry(c: &Common) -> bool {
    match run::live_gate(c.live, std::env::var(run::LIVE_ENV).ok().as_deref(), c.keypair.exists()) {
        Ok(()) => false,
        Err(why) => {
            tracing::info!(%why, "dry run");
            true
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .json()
        .with_current_span(false)
        .init();
    let cli = Cli::parse();
    match cli.cmd {
        Cmd::Run {
            common,
            ws,
            poll_rpc,
            poll_hz,
            aux_rpc,
            grpc,
            grpc_token,
            seed,
            families,
            per_family,
            batches_per_target,
            horizon_slots,
            lead_slots,
            payer_pool,
            cu_limit,
            loaded_data_limit,
            min_stake_sol,
            collect_wait_s,
            pilot,
        } => {
            let dir = run::run(run::RunArgs {
                data_dir: common.data,
                live_flag: common.live,
                keypair: common.keypair,
                rpc_url: common.rpc,
                ws_url: ws,
                poll_url: poll_rpc,
                poll_hz,
                aux_rpc_url: aux_rpc,
                grpc_url: grpc.filter(|s| !s.is_empty()),
                grpc_token: grpc_token.filter(|s| !s.is_empty()),
                cap_lamports: common.cap,
                seed,
                families,
                per_family,
                batches_per_target,
                horizon_slots,
                lead_slots,
                payer_pool,
                cu_limit,
                loaded_data_limit,
                pilot,
                min_stake_sol,
                collect_wait_s,
            })
            .await?;
            println!("{}", dir.display());
        }
        Cmd::Collect { common, run_dir } => {
            let rpc = Rpc::new(&common.rpc);
            let s = collect::collect(&rpc, &common.data, &run_dir).await?;
            io::write_json(run_dir.join("collect.json"), &s)?;
            println!("{}", serde_json::to_string_pretty(&s)?);
        }
        Cmd::Verify { common, out } => {
            let rpc = Rpc::new(&common.rpc);
            let r = verify::verify(&rpc, &common.data).await?;
            io::write_json(&out, &r)?;
            println!("{}", serde_json::to_string_pretty(&r)?);
            if !r.mismatches.is_empty() || !r.entry_mismatches.is_empty() {
                std::process::exit(1);
            }
        }
        Cmd::Fund { common, payers, lamports_each } => {
            let dry = live_or_dry(&common);
            let master = keys::load_keypair(&common.keypair)?;
            let rpc = Rpc::new(&common.rpc);
            let mut l = ledger::Ledger::open(common.data.join("ledger/spend.jsonl"), common.cap, "fund")?;
            println!("{}", serde_json::to_string_pretty(&fund::fund(&rpc, &mut l, &master, payers, lamports_each, dry).await?)?);
        }
        Cmd::Sweep { common, payers } => {
            let dry = live_or_dry(&common);
            let master = keys::load_keypair(&common.keypair)?;
            let rpc = Rpc::new(&common.rpc);
            let mut l = ledger::Ledger::open(common.data.join("ledger/spend.jsonl"), common.cap, "sweep")?;
            println!("{}", serde_json::to_string_pretty(&fund::sweep(&rpc, &mut l, &master, payers, dry).await?)?);
        }
        Cmd::Reconcile { common } => {
            let rpc = Rpc::new(&common.rpc);
            let path = common.data.join("ledger/spend.jsonl");
            let mut l = ledger::Ledger::open(&path, common.cap, "reconcile")?;
            println!("{}", serde_json::to_string_pretty(&precedence::reconcile::reconcile(&rpc, &mut l, &path).await?)?);
        }
        Cmd::Status { common, payers } => {
            let rpc = Rpc::new(&common.rpc);
            let master = keys::load_keypair(&common.keypair).context("status needs the keypair")?;
            let mut rows = Vec::new();
            let mut total = rpc.balance(&master.pubkey().to_string()).await?;
            for p in keys::derive_payers(&master, payers) {
                let b = rpc.balance(&p.pubkey().to_string()).await?;
                total += b;
                rows.push(json!({"payer": p.pubkey().to_string(), "lamports": b}));
            }
            let l = ledger::Ledger::open(common.data.join("ledger/spend.jsonl"), common.cap, "status")?;
            println!(
                "{}",
                serde_json::to_string_pretty(&json!({
                    "master": master.pubkey().to_string(),
                    "payers": rows,
                    "total_lamports": total,
                    "ledger": l.totals(),
                    "remaining_under_cap": l.remaining(),
                }))?
            );
        }
        Cmd::Calibrate { common } => {
            let rpc = Rpc::new(&common.rpc);
            let master = keys::load_keypair(&common.keypair)?;
            println!("{}", serde_json::to_string_pretty(&precedence::calibrate::calibrate(&rpc, &master).await?)?);
        }
        Cmd::Selftest { rpc, data, reps, seed, cu_limit, loaded_data_limit } => {
            let dir = precedence::selftest::selftest(precedence::selftest::SelftestArgs {
                rpc_url: rpc,
                data_dir: data,
                reps,
                seed,
                cu_limit,
                loaded_data_limit,
            })
            .await?;
            println!("{}", dir.display());
        }
        Cmd::LocalnetEntries { ledger, run, tool } => {
            let n = precedence::selftest::dump_entries(&ledger, &run, &tool)?;
            println!("{n} entries");
        }
        Cmd::EntriesProbe { grpc, grpc_token, seconds } => {
            println!("{}", serde_json::to_string_pretty(&yellowstone::probe(&grpc, grpc_token, seconds).await?)?);
        }
    }
    Ok(())
}

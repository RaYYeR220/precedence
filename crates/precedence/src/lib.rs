//! Precedence: send our own transactions into chosen Solana mainnet leader
//! windows and record everything needed to say, from the blocks, by what rule
//! each leader ordered them.

pub mod clock;
pub mod cost;
pub mod design;
pub mod io;
pub mod ledger;
pub mod rpc;
pub mod send;
pub mod snapshot;
pub mod targets;
pub mod tx;

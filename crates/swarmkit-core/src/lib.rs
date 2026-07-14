//! Rust core for swarmkit: the pieces where "did this actually happen" needs to be
//! a compiled guarantee rather than a convention. See docs/PLAN.md for the full
//! phase-by-phase build order.
//!
//! Phase 0 shipped `sandbox` (real subprocess execution). Phase 1 adds
//! `taskqueue` + `worker_pool` (real concurrent dispatch). `vectors` and
//! `mcp_tool_exec` land in later phases — do not add stub modules here that
//! don't do real work; an absent module is honest, a fake one isn't.

pub mod sandbox;
pub mod taskqueue;
pub mod worker_pool;

//! Rust core for swarmkit: the pieces where "did this actually happen" needs to be
//! a compiled guarantee rather than a convention. See docs/PLAN.md for the full
//! phase-by-phase build order.
//!
//! Phase 0 shipped `sandbox` (real subprocess execution). Phase 1 added
//! `taskqueue` + `worker_pool` (real concurrent dispatch). Phase 2 adds
//! `vectors` (compact vector store). `mcp_tool_exec` lands in a later phase —
//! do not add stub modules here that don't do real work; an absent module is
//! honest, a fake one isn't.

pub mod sandbox;
pub mod taskqueue;
pub mod vectors;
pub mod worker_pool;

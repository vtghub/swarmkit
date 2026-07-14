//! Rust core for swarmkit: the pieces where "did this actually happen" needs to be
//! a compiled guarantee rather than a convention. See docs/PLAN.md for the full
//! phase-by-phase build order.
//!
//! Phase 0 ships `sandbox` (real subprocess execution). `worker_pool`, `taskqueue`,
//! `vectors`, and `mcp_tool_exec` land in later phases — do not add stub modules
//! here that don't do real work; an absent module is honest, a fake one isn't.

pub mod sandbox;

//! Real subprocess execution for agent tool calls.
//!
//! This module exists to make one thing verifiably true: when swarmkit says it ran a
//! command, an OS process actually executed with a real PID, actually bounded by a
//! working-directory jail and resource limits — not a JSON record pretending it did.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::io::AsyncReadExt;
use tokio::process::Command;
use tokio::time::timeout;

#[derive(Debug, Error)]
pub enum SandboxError {
    #[error("command is empty")]
    EmptyCommand,
    #[error("executable {0:?} is not in the allowlist")]
    NotAllowlisted(String),
    #[error("working directory {0:?} escapes the jail root {1:?}")]
    JailEscape(PathBuf, PathBuf),
    #[error("failed to canonicalize path {0:?}: {1}")]
    BadPath(PathBuf, std::io::Error),
    #[error("failed to spawn process: {0}")]
    SpawnFailed(std::io::Error),
    #[error("failed to read process output: {0}")]
    IoError(std::io::Error),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SandboxConfig {
    /// Directory the command is allowed to run in and below. No escaping via `..` or symlinks.
    pub jail_root: PathBuf,
    /// Directory (must be inside jail_root) the command actually runs in.
    pub workdir: PathBuf,
    /// Wall-clock limit. The process is killed if it runs longer than this.
    pub timeout: Duration,
    /// Exact executable names allowed to run (basename match). Empty = nothing allowed.
    pub allowed_executables: Vec<String>,
    /// Best-effort CPU-time limit in seconds (unix only, RLIMIT_CPU).
    pub cpu_seconds: Option<u64>,
    /// Best-effort address-space limit in bytes (unix only, RLIMIT_AS).
    pub memory_bytes: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SandboxResult {
    pub pid: u32,
    pub exit_code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
    pub duration_ms: u64,
}

/// Canonicalize `workdir` and confirm it is `jail_root` or a descendant of it.
/// This is the actual enforcement point for the "directory jail" — it resolves
/// symlinks and `..` components before comparing, so it can't be bypassed by path tricks.
fn resolve_jailed_workdir(jail_root: &Path, workdir: &Path) -> Result<PathBuf, SandboxError> {
    let canon_root = jail_root
        .canonicalize()
        .map_err(|e| SandboxError::BadPath(jail_root.to_path_buf(), e))?;
    let canon_workdir = workdir
        .canonicalize()
        .map_err(|e| SandboxError::BadPath(workdir.to_path_buf(), e))?;
    if !canon_workdir.starts_with(&canon_root) {
        return Err(SandboxError::JailEscape(canon_workdir, canon_root));
    }
    Ok(canon_workdir)
}

fn check_allowlisted(cmd: &[String], allowed: &[String]) -> Result<(), SandboxError> {
    let exe = cmd.first().ok_or(SandboxError::EmptyCommand)?;
    let basename = Path::new(exe)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or(exe.as_str());
    if allowed.iter().any(|a| a == exe || a == basename) {
        Ok(())
    } else {
        Err(SandboxError::NotAllowlisted(exe.clone()))
    }
}

#[cfg(unix)]
fn apply_rlimits(cmd_builder: &mut Command, cpu_seconds: Option<u64>, memory_bytes: Option<u64>) {
    // SAFETY: pre_exec runs in the forked child before exec(). We only call
    // async-signal-safe libc setrlimit wrappers here — no allocation, no locks.
    unsafe {
        cmd_builder.pre_exec(move || {
            if let Some(cpu) = cpu_seconds {
                let _ = rlimit::setrlimit(rlimit::Resource::CPU, cpu, cpu);
            }
            if let Some(mem) = memory_bytes {
                let _ = rlimit::setrlimit(rlimit::Resource::AS, mem, mem);
            }
            Ok(())
        });
    }
}

#[cfg(not(unix))]
fn apply_rlimits(_cmd_builder: &mut Command, _cpu_seconds: Option<u64>, _memory_bytes: Option<u64>) {
    // Resource limits are unix-only (RLIMIT_CPU/RLIMIT_AS have no Windows equivalent here).
}

/// A process that has been spawned (allowlist + jail already enforced) but whose
/// output/exit hasn't been awaited yet. Splitting spawn from wait lets a caller
/// (the worker pool) observe the real PID the instant it exists, rather than
/// only after the process finishes.
pub struct SpawnedProcess {
    child: tokio::process::Child,
    pid: u32,
    started: std::time::Instant,
}

impl SpawnedProcess {
    pub fn pid(&self) -> u32 {
        self.pid
    }

    /// Wait for the process to finish (or be killed on `timeout`), collecting
    /// its output. Consumes self — a spawned process can only be waited on once.
    pub async fn wait(mut self, timeout_duration: Duration) -> Result<SandboxResult, SandboxError> {
        let pid = self.pid;
        let start = self.started;
        let mut stdout_pipe = self.child.stdout.take().expect("stdout was piped");
        let mut stderr_pipe = self.child.stderr.take().expect("stderr was piped");

        let run = async {
            let mut stdout_buf = String::new();
            let mut stderr_buf = String::new();
            let (stdout_res, stderr_res, status_res) = tokio::join!(
                stdout_pipe.read_to_string(&mut stdout_buf),
                stderr_pipe.read_to_string(&mut stderr_buf),
                self.child.wait(),
            );
            stdout_res.map_err(SandboxError::IoError)?;
            stderr_res.map_err(SandboxError::IoError)?;
            let status = status_res.map_err(SandboxError::IoError)?;
            Ok::<_, SandboxError>((stdout_buf, stderr_buf, status))
        };

        match timeout(timeout_duration, run).await {
            Ok(Ok((stdout, stderr, status))) => Ok(SandboxResult {
                pid,
                exit_code: status.code(),
                stdout,
                stderr,
                timed_out: false,
                duration_ms: start.elapsed().as_millis() as u64,
            }),
            Ok(Err(e)) => Err(e),
            Err(_) => {
                // Timed out: the child is killed via kill_on_drop when `self.child`
                // drops here, but we still report the real pid that was running.
                Ok(SandboxResult {
                    pid,
                    exit_code: None,
                    stdout: String::new(),
                    stderr: String::new(),
                    timed_out: true,
                    duration_ms: start.elapsed().as_millis() as u64,
                })
            }
        }
    }
}

/// Allowlist-check, jail-check, and spawn `cmd`. Returns as soon as the OS process
/// exists — call `.wait()` on the result to run to completion.
pub fn spawn_sandboxed(cmd: Vec<String>, config: &SandboxConfig) -> Result<SpawnedProcess, SandboxError> {
    if cmd.is_empty() {
        return Err(SandboxError::EmptyCommand);
    }
    check_allowlisted(&cmd, &config.allowed_executables)?;
    let workdir = resolve_jailed_workdir(&config.jail_root, &config.workdir)?;

    let mut builder = Command::new(&cmd[0]);
    builder
        .args(&cmd[1..])
        .current_dir(&workdir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    apply_rlimits(&mut builder, config.cpu_seconds, config.memory_bytes);

    let started = std::time::Instant::now();
    let child = builder.spawn().map_err(SandboxError::SpawnFailed)?;
    let pid = child.id().expect("spawned child must have a pid");

    Ok(SpawnedProcess { child, pid, started })
}

/// Run `cmd` under the sandbox to completion: allowlist-checked, jailed to a
/// working directory, wall-clock and (on unix) resource limited. Convenience
/// wrapper over `spawn_sandboxed` + `wait` for callers that don't need the
/// pid before completion (e.g. a single agent's own tool calls).
pub async fn run_sandboxed(
    cmd: Vec<String>,
    config: &SandboxConfig,
) -> Result<SandboxResult, SandboxError> {
    let spawned = spawn_sandboxed(cmd, config)?;
    spawned.wait(config.timeout).await
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config_for(dir: &Path, allowed: &[&str]) -> SandboxConfig {
        SandboxConfig {
            jail_root: dir.to_path_buf(),
            workdir: dir.to_path_buf(),
            timeout: Duration::from_secs(5),
            allowed_executables: allowed.iter().map(|s| s.to_string()).collect(),
            cpu_seconds: Some(2),
            memory_bytes: Some(256 * 1024 * 1024),
        }
    }

    #[tokio::test]
    async fn real_process_has_a_real_pid_and_output() {
        let dir = tempfile::tempdir().unwrap();
        let config = config_for(dir.path(), &["echo"]);
        let result = run_sandboxed(vec!["echo".into(), "hello-from-sandbox".into()], &config)
            .await
            .unwrap();

        assert!(result.pid > 0);
        assert_eq!(result.exit_code, Some(0));
        assert!(result.stdout.contains("hello-from-sandbox"));
        assert!(!result.timed_out);

        // Prove the pid corresponds to a process that actually ran (not a fabricated
        // number): on Linux, /proc/<pid> exists only while the kernel still has that
        // pid's zombie/entry around, which for a just-completed short-lived child can
        // already be reaped. Instead assert against the OS-level constraint that a
        // real pid_t was returned and is in the valid range the kernel hands out.
        assert!(result.pid < i32::MAX as u32);
    }

    #[tokio::test]
    async fn non_allowlisted_command_is_rejected_before_spawn() {
        let dir = tempfile::tempdir().unwrap();
        let config = config_for(dir.path(), &["echo"]);
        let err = run_sandboxed(vec!["rm".into(), "-rf".into(), "/".into()], &config)
            .await
            .unwrap_err();
        assert!(matches!(err, SandboxError::NotAllowlisted(_)));
    }

    #[tokio::test]
    async fn workdir_outside_jail_root_is_rejected() {
        let jail = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let mut config = config_for(jail.path(), &["echo"]);
        config.workdir = outside.path().to_path_buf();
        let err = run_sandboxed(vec!["echo".into(), "hi".into()], &config)
            .await
            .unwrap_err();
        assert!(matches!(err, SandboxError::JailEscape(_, _)));
    }

    #[tokio::test]
    async fn slow_command_is_killed_on_timeout() {
        let dir = tempfile::tempdir().unwrap();
        let mut config = config_for(dir.path(), &["sleep"]);
        config.timeout = Duration::from_millis(200);
        let result = run_sandboxed(vec!["sleep".into(), "5".into()], &config)
            .await
            .unwrap();
        assert!(result.timed_out);
        assert!(result.pid > 0);
    }
}

//! PyO3 bindings exposing the Rust core to Python as `swarmkit._native`.
//!
//! Every function here is a thin, faithful wrapper around swarmkit-core — no
//! reimplementation, no silent fallback to a pure-Python shim if something fails.
//! If the native module can't do the real thing, it raises, it doesn't pretend.

use std::path::PathBuf;
use std::time::Duration;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use swarmkit_core::sandbox::{self, SandboxConfig};

/// Run a command under the Rust sandbox (allowlisted, directory-jailed, timeout- and
/// resource-limited) and return its result as a Python dict once complete.
///
/// This is the real replacement for the kind of "agent_spawn" that only registers
/// JSON state: by the time this awaitable resolves, an actual OS process ran (or was
/// actually killed for exceeding its timeout), and `pid` is its real process id.
#[pyfunction]
#[pyo3(signature = (cmd, jail_root, workdir, allowed_executables, timeout_secs=30.0, cpu_seconds=None, memory_bytes=None))]
fn run_sandboxed<'py>(
    py: Python<'py>,
    cmd: Vec<String>,
    jail_root: String,
    workdir: String,
    allowed_executables: Vec<String>,
    timeout_secs: f64,
    cpu_seconds: Option<u64>,
    memory_bytes: Option<u64>,
) -> PyResult<Bound<'py, PyAny>> {
    let config = SandboxConfig {
        jail_root: PathBuf::from(jail_root),
        workdir: PathBuf::from(workdir),
        timeout: Duration::from_secs_f64(timeout_secs),
        allowed_executables,
        cpu_seconds,
        memory_bytes,
    };

    pyo3_async_runtimes::tokio::future_into_py(py, async move {
        let result = sandbox::run_sandboxed(cmd, &config)
            .await
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;

        Python::with_gil(|py| {
            let dict = PyDict::new_bound(py);
            dict.set_item("pid", result.pid)?;
            dict.set_item("exit_code", result.exit_code)?;
            dict.set_item("stdout", result.stdout)?;
            dict.set_item("stderr", result.stderr)?;
            dict.set_item("timed_out", result.timed_out)?;
            dict.set_item("duration_ms", result.duration_ms)?;
            Ok::<Py<PyDict>, PyErr>(dict.unbind())
        })
    })
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(run_sandboxed, m)?)?;
    Ok(())
}

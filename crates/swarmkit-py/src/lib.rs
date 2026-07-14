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

use swarmkit_core::sandbox::{self, SandboxConfig, SandboxResult};
use swarmkit_core::taskqueue::TaskStatus;
use swarmkit_core::worker_pool::WorkerPool as CoreWorkerPool;

fn sandbox_result_to_dict(py: Python<'_>, result: &SandboxResult) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new_bound(py);
    dict.set_item("pid", result.pid)?;
    dict.set_item("exit_code", result.exit_code)?;
    dict.set_item("stdout", &result.stdout)?;
    dict.set_item("stderr", &result.stderr)?;
    dict.set_item("timed_out", result.timed_out)?;
    dict.set_item("duration_ms", result.duration_ms)?;
    Ok(dict.unbind())
}

fn task_status_to_dict(py: Python<'_>, status: &TaskStatus) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new_bound(py);
    match status {
        TaskStatus::Queued => {
            dict.set_item("status", "queued")?;
        }
        TaskStatus::Running { pid } => {
            dict.set_item("status", "running")?;
            dict.set_item("pid", pid)?;
        }
        TaskStatus::Completed { result } => {
            dict.set_item("status", "completed")?;
            dict.set_item("result", sandbox_result_to_dict(py, result)?)?;
        }
        TaskStatus::Failed { error } => {
            dict.set_item("status", "failed")?;
            dict.set_item("error", error)?;
        }
    }
    Ok(dict.unbind())
}

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
        Python::with_gil(|py| sandbox_result_to_dict(py, &result))
    })
}

/// A pool of `concurrency` real workers, each executing sandboxed subprocess
/// jobs pulled off one shared queue. This is the Phase 1 fix for Ruflo's fake
/// `agent_spawn`: `submit` returns a task id immediately, `status` reflects a
/// real OS pid the moment a worker actually starts the process, and N jobs at
/// concurrency >= N run genuinely in parallel (see tests/unit/test_worker_pool.py).
#[pyclass(name = "WorkerPool")]
struct PyWorkerPool {
    inner: CoreWorkerPool,
}

#[pymethods]
impl PyWorkerPool {
    #[new]
    fn new(concurrency: usize) -> PyResult<Self> {
        // `Runtime::spawn` (via the shared runtime's Handle) works from a plain
        // synchronous constructor — unlike the `tokio::spawn` free function, it
        // doesn't require the calling code to already be running inside the
        // runtime it targets.
        let handle = pyo3_async_runtimes::tokio::get_runtime().handle().clone();
        Ok(Self {
            inner: CoreWorkerPool::spawn(concurrency, &handle),
        })
    }

    #[pyo3(signature = (cmd, jail_root, workdir, allowed_executables, timeout_secs=30.0, cpu_seconds=None, memory_bytes=None))]
    fn submit<'py>(
        &self,
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
        let queue = self.inner.queue().clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let task_id = queue.submit(cmd, config).await;
            Ok(task_id)
        })
    }

    fn status<'py>(&self, py: Python<'py>, task_id: String) -> PyResult<Bound<'py, PyAny>> {
        let queue = self.inner.queue().clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let status = queue.status(&task_id).await;
            Python::with_gil(|py| match status {
                Some(s) => Ok(task_status_to_dict(py, &s)?.into_py(py)),
                None => Ok(py.None()),
            })
        })
    }

    fn list_tasks<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let queue = self.inner.queue().clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let tasks = queue.list().await;
            Python::with_gil(|py| {
                let out = pyo3::types::PyList::empty_bound(py);
                for (id, status) in tasks {
                    let pair = pyo3::types::PyTuple::new_bound(
                        py,
                        &[id.into_py(py), task_status_to_dict(py, &status)?.into_py(py)],
                    );
                    out.append(pair)?;
                }
                Ok(out.unbind())
            })
        })
    }
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(run_sandboxed, m)?)?;
    m.add_class::<PyWorkerPool>()?;
    Ok(())
}

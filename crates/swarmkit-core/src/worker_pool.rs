//! A real worker pool: `concurrency` tokio tasks, each pulling jobs off one
//! shared queue and executing them as actual sandboxed OS subprocesses. N
//! submitted jobs at `concurrency` >= N genuinely run in parallel (see the
//! concurrency proof test in `tests/unit/test_worker_pool.py`), and
//! `TaskStatus::Running` carries a real OS pid the moment the process starts.

use std::sync::Arc;

use tokio::runtime::Handle;
use tokio::sync::Mutex;

use crate::sandbox;
use crate::taskqueue::{TaskQueue, TaskStatus};

pub struct WorkerPool {
    queue: TaskQueue,
}

impl WorkerPool {
    /// Spawn `concurrency` worker loops onto `handle`. `handle.spawn` (unlike the
    /// `tokio::spawn` free function) doesn't require the caller to already be
    /// running inside the runtime, so this can be called from a synchronous
    /// PyO3 constructor.
    pub fn spawn(concurrency: usize, handle: &Handle) -> Self {
        let (queue, rx) = TaskQueue::new();
        let rx = Arc::new(Mutex::new(rx));

        for _ in 0..concurrency.max(1) {
            let rx = rx.clone();
            let queue = queue.clone();
            handle.spawn(async move {
                loop {
                    let job = { rx.lock().await.recv().await };
                    let Some(job) = job else {
                        break; // queue closed (all senders dropped) — shut this worker down
                    };
                    let timeout = job.config.timeout;
                    match sandbox::spawn_sandboxed(job.cmd, &job.config) {
                        Ok(spawned) => {
                            queue
                                .set_status(&job.id, TaskStatus::Running { pid: spawned.pid() })
                                .await;
                            match spawned.wait(timeout).await {
                                Ok(result) => {
                                    queue
                                        .set_status(&job.id, TaskStatus::Completed { result })
                                        .await
                                }
                                Err(e) => {
                                    queue
                                        .set_status(
                                            &job.id,
                                            TaskStatus::Failed {
                                                error: e.to_string(),
                                            },
                                        )
                                        .await
                                }
                            }
                        }
                        Err(e) => {
                            queue
                                .set_status(
                                    &job.id,
                                    TaskStatus::Failed {
                                        error: e.to_string(),
                                    },
                                )
                                .await
                        }
                    }
                }
            });
        }

        Self { queue }
    }

    pub fn queue(&self) -> &TaskQueue {
        &self.queue
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sandbox::SandboxConfig;
    use std::time::Duration;

    fn config_for(dir: &std::path::Path, allowed: &[&str], timeout: Duration) -> SandboxConfig {
        SandboxConfig {
            jail_root: dir.to_path_buf(),
            workdir: dir.to_path_buf(),
            timeout,
            allowed_executables: allowed.iter().map(|s| s.to_string()).collect(),
            cpu_seconds: None,
            memory_bytes: None,
        }
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn n_tasks_at_concurrency_n_run_in_parallel_not_serially() {
        let dir = tempfile::tempdir().unwrap();
        let n = 5usize;
        let sleep_secs = 0.4;
        let pool = WorkerPool::spawn(n, &Handle::current());
        let config = config_for(dir.path(), &["sleep"], Duration::from_secs(5));

        let start = std::time::Instant::now();
        let mut ids = Vec::new();
        for _ in 0..n {
            let id = pool
                .queue()
                .submit(vec!["sleep".into(), sleep_secs.to_string()], config.clone())
                .await;
            ids.push(id);
        }

        for id in &ids {
            loop {
                match pool.queue().status(id).await {
                    Some(TaskStatus::Completed { .. }) => break,
                    Some(TaskStatus::Failed { error }) => panic!("task failed: {error}"),
                    _ => tokio::time::sleep(Duration::from_millis(10)).await,
                }
            }
        }
        let elapsed = start.elapsed().as_secs_f64();

        assert!(
            elapsed < sleep_secs * (n as f64) / 2.0,
            "expected concurrent completion well under {:.2}s (n * sleep), got {:.2}s",
            sleep_secs * n as f64,
            elapsed
        );
    }
}

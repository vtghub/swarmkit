//! The task registry and submission queue behind the worker pool. A `TaskQueue`
//! is cheap to clone (it's just a channel sender + a shared status map) so both
//! the submitting side and each worker hold their own handle to the same state.

use std::collections::HashMap;
use std::sync::Arc;

use tokio::sync::{mpsc, Mutex};
use uuid::Uuid;

use crate::sandbox::{SandboxConfig, SandboxResult};

pub type TaskId = String;

#[derive(Debug, Clone)]
pub enum TaskStatus {
    Queued,
    Running { pid: u32 },
    Completed { result: SandboxResult },
    Failed { error: String },
}

pub struct Job {
    pub id: TaskId,
    pub cmd: Vec<String>,
    pub config: SandboxConfig,
}

#[derive(Clone)]
pub struct TaskQueue {
    sender: mpsc::UnboundedSender<Job>,
    registry: Arc<Mutex<HashMap<TaskId, TaskStatus>>>,
}

impl TaskQueue {
    pub(crate) fn new() -> (Self, mpsc::UnboundedReceiver<Job>) {
        let (tx, rx) = mpsc::unbounded_channel();
        (
            Self {
                sender: tx,
                registry: Arc::new(Mutex::new(HashMap::new())),
            },
            rx,
        )
    }

    /// Enqueue a job and return its id immediately. The job is recorded as
    /// `Queued` before the send, so a status lookup can never race a task
    /// that "doesn't exist yet" from the caller's point of view.
    pub async fn submit(&self, cmd: Vec<String>, config: SandboxConfig) -> TaskId {
        let id = Uuid::new_v4().to_string();
        self.registry.lock().await.insert(id.clone(), TaskStatus::Queued);
        // An error here means every worker has been dropped; the task stays
        // Queued forever, which a status lookup surfaces rather than losing silently.
        let _ = self.sender.send(Job {
            id: id.clone(),
            cmd,
            config,
        });
        id
    }

    pub async fn status(&self, id: &str) -> Option<TaskStatus> {
        self.registry.lock().await.get(id).cloned()
    }

    pub(crate) async fn set_status(&self, id: &str, status: TaskStatus) {
        self.registry.lock().await.insert(id.to_string(), status);
    }

    pub async fn list(&self) -> Vec<(TaskId, TaskStatus)> {
        self.registry
            .lock()
            .await
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect()
    }
}

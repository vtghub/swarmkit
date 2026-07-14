//! Compact in-process vector store: a fixed-width binary on-disk format (no
//! per-entry JSON/object framing) plus an in-memory HNSW index
//! (`instant-distance`) rebuilt from that compact source of truth.
//!
//! `instant-distance` only supports building an index from a fixed batch, not
//! incremental insertion, so the index is treated as a cache: it's rebuilt
//! lazily the next time `search` is called after any `add`. For the memory
//! scales a single agent/swarm accumulates (thousands, not millions, of
//! entries) a full rebuild is low-single-digit milliseconds — real HNSW
//! search quality without pretending the crate supports incremental updates
//! it doesn't.

use std::path::Path;

use instant_distance::{Builder, HnswMap, Point as HnswPoint, Search};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum VectorError {
    #[error("vector dimension mismatch: store expects {expected}, got {got}")]
    DimensionMismatch { expected: usize, got: usize },
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("corrupt vector store file: {0}")]
    Corrupt(String),
}

#[derive(Clone)]
struct EmbeddingPoint(Vec<f32>);

impl HnswPoint for EmbeddingPoint {
    fn distance(&self, other: &Self) -> f32 {
        // Cosine distance (1 - cosine similarity): 0 = identical direction, 2 = opposite.
        let dot: f32 = self.0.iter().zip(other.0.iter()).map(|(a, b)| a * b).sum();
        let norm_a = self.0.iter().map(|x| x * x).sum::<f32>().sqrt();
        let norm_b = other.0.iter().map(|x| x * x).sum::<f32>().sqrt();
        if norm_a == 0.0 || norm_b == 0.0 {
            return 1.0;
        }
        1.0 - (dot / (norm_a * norm_b))
    }
}

pub struct VectorStore {
    /// Source of truth: (memory id, embedding). Persisted verbatim to disk.
    entries: Vec<(String, Vec<f32>)>,
    dim: Option<usize>,
    index: Option<HnswMap<EmbeddingPoint, String>>,
    index_stale: bool,
}

impl VectorStore {
    pub fn new() -> Self {
        Self {
            entries: Vec::new(),
            dim: None,
            index: None,
            index_stale: true,
        }
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Insert or replace the vector for `id`. All vectors in a store must share
    /// one dimension, fixed by the first insert.
    pub fn add(&mut self, id: String, vector: Vec<f32>) -> Result<(), VectorError> {
        match self.dim {
            Some(dim) if dim != vector.len() => {
                return Err(VectorError::DimensionMismatch {
                    expected: dim,
                    got: vector.len(),
                })
            }
            Some(_) => {}
            None => self.dim = Some(vector.len()),
        }
        self.entries.retain(|(existing, _)| existing != &id); // upsert
        self.entries.push((id, vector));
        self.index_stale = true;
        Ok(())
    }

    fn ensure_index(&mut self) {
        if !self.index_stale && self.index.is_some() {
            return;
        }
        if self.entries.is_empty() {
            self.index = None;
        } else {
            let points: Vec<EmbeddingPoint> = self
                .entries
                .iter()
                .map(|(_, v)| EmbeddingPoint(v.clone()))
                .collect();
            let ids: Vec<String> = self.entries.iter().map(|(id, _)| id.clone()).collect();
            self.index = Some(Builder::default().build(points, ids));
        }
        self.index_stale = false;
    }

    /// Return up to `k` nearest neighbors as (id, cosine_distance), closest first.
    pub fn search(&mut self, query: &[f32], k: usize) -> Result<Vec<(String, f32)>, VectorError> {
        if let Some(dim) = self.dim {
            if query.len() != dim {
                return Err(VectorError::DimensionMismatch {
                    expected: dim,
                    got: query.len(),
                });
            }
        }
        self.ensure_index();
        let Some(index) = &self.index else {
            return Ok(Vec::new());
        };
        let mut search = Search::default();
        let point = EmbeddingPoint(query.to_vec());
        Ok(index
            .search(&point, &mut search)
            .take(k)
            .map(|item| (item.value.clone(), item.distance))
            .collect())
    }

    /// Fixed-width binary format: [dim: u32][count: u32]{[id_len: u32][id bytes][dim * f32]}*
    /// No JSON, no per-entry object framing — keeps on-disk size low and
    /// predictable regardless of entry count.
    pub fn save(&self, path: &Path) -> Result<(), VectorError> {
        let dim = self.dim.unwrap_or(0) as u32;
        let mut buf = Vec::with_capacity(self.on_disk_bytes());
        buf.extend_from_slice(&dim.to_le_bytes());
        buf.extend_from_slice(&(self.entries.len() as u32).to_le_bytes());
        for (id, vector) in &self.entries {
            let id_bytes = id.as_bytes();
            buf.extend_from_slice(&(id_bytes.len() as u32).to_le_bytes());
            buf.extend_from_slice(id_bytes);
            for f in vector {
                buf.extend_from_slice(&f.to_le_bytes());
            }
        }
        std::fs::write(path, buf)?;
        Ok(())
    }

    pub fn load(path: &Path) -> Result<Self, VectorError> {
        let buf = std::fs::read(path)?;
        let mut offset = 0usize;
        let read_u32 = |buf: &[u8], offset: &mut usize| -> Result<u32, VectorError> {
            let bytes = buf
                .get(*offset..*offset + 4)
                .ok_or_else(|| VectorError::Corrupt("truncated u32".into()))?;
            *offset += 4;
            Ok(u32::from_le_bytes(bytes.try_into().unwrap()))
        };

        let dim = read_u32(&buf, &mut offset)? as usize;
        let count = read_u32(&buf, &mut offset)? as usize;
        let mut entries = Vec::with_capacity(count);
        for _ in 0..count {
            let id_len = read_u32(&buf, &mut offset)? as usize;
            let id_bytes = buf
                .get(offset..offset + id_len)
                .ok_or_else(|| VectorError::Corrupt("truncated id".into()))?;
            let id = String::from_utf8(id_bytes.to_vec())
                .map_err(|e| VectorError::Corrupt(format!("invalid utf8 id: {e}")))?;
            offset += id_len;

            let mut vector = Vec::with_capacity(dim);
            for _ in 0..dim {
                let f_bytes = buf
                    .get(offset..offset + 4)
                    .ok_or_else(|| VectorError::Corrupt("truncated vector".into()))?;
                vector.push(f32::from_le_bytes(f_bytes.try_into().unwrap()));
                offset += 4;
            }
            entries.push((id, vector));
        }

        Ok(Self {
            entries,
            dim: if dim > 0 { Some(dim) } else { None },
            index: None,
            index_stale: true,
        })
    }

    /// Exact byte size of the on-disk format `save` produces — the metric the
    /// bytes-per-entry benchmark tracks over time.
    pub fn on_disk_bytes(&self) -> usize {
        let dim = self.dim.unwrap_or(0);
        8 + self
            .entries
            .iter()
            .map(|(id, _)| 4 + id.len() + dim * 4)
            .sum::<usize>()
    }
}

impl Default for VectorStore {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(vals: &[f32]) -> Vec<f32> {
        vals.to_vec()
    }

    #[test]
    fn search_returns_closest_by_cosine_distance() {
        let mut store = VectorStore::new();
        store.add("a".into(), v(&[1.0, 0.0, 0.0])).unwrap();
        store.add("b".into(), v(&[0.0, 1.0, 0.0])).unwrap();
        store.add("c".into(), v(&[0.9, 0.1, 0.0])).unwrap();

        let results = store.search(&[1.0, 0.0, 0.0], 2).unwrap();
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].0, "a");
        assert!(results[0].1 < results[1].1, "closest match should have smallest distance");
    }

    #[test]
    fn upsert_replaces_existing_id() {
        let mut store = VectorStore::new();
        store.add("a".into(), v(&[1.0, 0.0])).unwrap();
        store.add("a".into(), v(&[0.0, 1.0])).unwrap();
        assert_eq!(store.len(), 1);
        let results = store.search(&[0.0, 1.0], 1).unwrap();
        assert_eq!(results[0].0, "a");
        assert!(results[0].1 < 0.01);
    }

    #[test]
    fn dimension_mismatch_is_rejected() {
        let mut store = VectorStore::new();
        store.add("a".into(), v(&[1.0, 0.0])).unwrap();
        let err = store.add("b".into(), v(&[1.0, 0.0, 0.0])).unwrap_err();
        assert!(matches!(err, VectorError::DimensionMismatch { .. }));
    }

    #[test]
    fn save_and_load_round_trip_preserves_search_results() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("store.bin");

        let mut store = VectorStore::new();
        store.add("a".into(), v(&[1.0, 0.0, 0.0])).unwrap();
        store.add("b".into(), v(&[0.0, 1.0, 0.0])).unwrap();
        store.save(&path).unwrap();

        let mut loaded = VectorStore::load(&path).unwrap();
        assert_eq!(loaded.len(), 2);
        let results = loaded.search(&[1.0, 0.0, 0.0], 1).unwrap();
        assert_eq!(results[0].0, "a");
    }

    #[test]
    fn on_disk_bytes_is_low_single_digit_kb_per_entry() {
        let mut store = VectorStore::new();
        let dim = 384; // matches all-MiniLM-L6-v2
        for i in 0..20 {
            let vector: Vec<f32> = (0..dim).map(|j| ((i * dim + j) as f32).sin()).collect();
            store.add(format!("memory-{i}"), vector).unwrap();
        }
        let bytes_per_entry = store.on_disk_bytes() as f64 / store.len() as f64;
        // dim*4 + id overhead bytes/entry — comfortably under 2KB regardless
        // of entry count, tracked here so a regression would be caught.
        assert!(
            bytes_per_entry < 2048.0,
            "expected < 2KB/entry, got {bytes_per_entry:.1} bytes/entry"
        );
    }
}

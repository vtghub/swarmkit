"""Hybrid memory retrieval: SQLite FTS5 keyword search + Rust vector
similarity, combined via Reciprocal Rank Fusion (RRF), then re-ranked with
Maximal Marginal Relevance (MMR) for result diversity.
"""

from __future__ import annotations

from dataclasses import dataclass

from swarmkit._native import VectorStore
from swarmkit.memory.embeddings import Embedder
from swarmkit.memory.store import MemoryRecord, MemoryStore

RRF_K = 60  # standard Reciprocal Rank Fusion constant


@dataclass
class RetrievedMemory:
    record: MemoryRecord
    score: float


class MemoryIndex:
    """Ties a MemoryStore (text + keyword search) and a VectorStore
    (embeddings + similarity search) into one add/retrieve API."""

    def __init__(self, store: MemoryStore, vectors: VectorStore, embedder: Embedder) -> None:
        self.store = store
        self.vectors = vectors
        self.embedder = embedder

    async def add(self, content: str) -> MemoryRecord:
        record = await self.store.add(content)
        [vector] = self.embedder.embed([content])
        self.vectors.add(record.id, vector)
        return record

    async def retrieve(
        self,
        query: str,
        k: int = 10,
        *,
        candidates: int = 50,
        lambda_mult: float = 0.5,
    ) -> list[RetrievedMemory]:
        keyword_hits = await self.store.keyword_search(query, k=candidates)
        [query_vector] = self.embedder.embed([query])
        vector_hits = self.vectors.search(query_vector, k=candidates)

        fused = reciprocal_rank_fusion(
            [record.id for record, _ in keyword_hits],
            [mem_id for mem_id, _ in vector_hits],
        )
        if not fused:
            return []

        records_by_id = {record.id: record for record, _ in keyword_hits}
        for mem_id in fused:
            if mem_id not in records_by_id:
                record = await self.store.get(mem_id)
                if record is not None:
                    records_by_id[mem_id] = record

        candidate_records = [records_by_id[mid] for mid in fused if mid in records_by_id]
        if not candidate_records:
            return []

        candidate_vectors = self.embedder.embed([r.content for r in candidate_records])
        selected = _mmr(query_vector, candidate_records, candidate_vectors, k=k, lambda_mult=lambda_mult)

        return [RetrievedMemory(record=r, score=fused[r.id]) for r in selected]


def reciprocal_rank_fusion(
    keyword_ids: list[str],
    vector_ids: list[str],
    *,
    k: int = RRF_K,
) -> dict[str, float]:
    """Combine two independently-ranked id lists into one fused score per id
    — the standard RRF formula. Shared by MemoryIndex (above) and
    memory/trajectories.py's TrajectoryStore, so every hybrid retriever in
    swarmkit combines keyword+vector rankings the same way."""
    scores: dict[str, float] = {}
    for rank, item_id in enumerate(keyword_ids):
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, item_id in enumerate(vector_ids):
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _mmr(
    query_vector: list[float],
    candidates: list[MemoryRecord],
    candidate_vectors: list[list[float]],
    *,
    k: int,
    lambda_mult: float,
) -> list[MemoryRecord]:
    selected_idx: list[int] = []
    remaining = list(range(len(candidates)))
    while remaining and len(selected_idx) < k:
        best_idx, best_score = None, float("-inf")
        for i in remaining:
            relevance = _cosine(query_vector, candidate_vectors[i])
            diversity = max(
                (_cosine(candidate_vectors[i], candidate_vectors[j]) for j in selected_idx),
                default=0.0,
            )
            mmr_score = lambda_mult * relevance - (1 - lambda_mult) * diversity
            if mmr_score > best_score:
                best_idx, best_score = i, mmr_score
        selected_idx.append(best_idx)
        remaining.remove(best_idx)
    return [candidates[i] for i in selected_idx]

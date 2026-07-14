"""Thin persistence wrapper around swarmkit._native.VectorStore: load from
disk if a file already exists, otherwise start empty. Callers still call
`.save()` explicitly — auto-saving on every insert would make N inserts cost
N full-file rewrites, which defeats the point of a compact storage format.
"""

from __future__ import annotations

from pathlib import Path

from swarmkit import _native


def open_vector_store(path: str | Path) -> "_native.VectorStore":
    path = Path(path)
    if path.exists():
        return _native.VectorStore.load(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    return _native.VectorStore()

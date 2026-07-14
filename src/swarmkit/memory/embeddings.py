"""Pluggable embedding backends.

`SentenceTransformerEmbedder` is the intended semantic-quality default. It's
an optional, lazily-imported dependency (`pip install 'swarmkit[embeddings]'`)
so the rest of swarmkit doesn't need PyTorch installed to function.

`HashingEmbedder` is a real, dependency-free technique — feature hashing of
tokens into a fixed-width vector, the same idea behind scikit-learn's
`HashingVectorizer` — not a stub. It's lower quality than a trained sentence
encoder but genuinely produces useful vectors for tests and offline use
where installing a multi-hundred-MB ML dependency isn't warranted.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one fixed-length, L2-normalized vector per input text."""


class HashingEmbedder(Embedder):
    """Deterministic, dependency-free bag-of-words hashing embedder."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = text.lower().split()
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector


class SentenceTransformerEmbedder(Embedder):
    """Real semantic embeddings via sentence-transformers. Raises a clear
    ImportError with install instructions if the optional dependency is
    missing, rather than silently falling back to a lower-quality embedder —
    a caller who asked for semantic embeddings and got hashing instead would
    have no way to know their retrieval quality quietly degraded."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "SentenceTransformerEmbedder requires the optional 'embeddings' "
                "extra: pip install 'swarmkit[embeddings]'"
            ) from e
        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()

"""Embeddings for clustering, overlap_check, and (via reuse) router matching.

The router should import get_embedder() from this module so every stage of
the system embeds text identically.

Resolution: OpenAI text-embedding-3-small when OPENAI_API_KEY is set (and
mock mode is not forced), otherwise a deterministic hashing embedder with no
network and no new dependencies.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

import numpy as np


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder (mock / offline mode).

    Same text in -> same vector out, always. Cosine similarity behaves like
    token-overlap similarity, which is good enough for the no-key demo path.
    """

    name = "hashing"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        mat = np.zeros((len(texts), self.dim), dtype=np.float64)
        for row, text in enumerate(texts):
            tokens = re.findall(r"[a-z0-9]+", text.lower())
            vec = np.zeros(self.dim, dtype=np.float64)
            for tok in tokens:
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                vec[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec /= norm
            mat[row] = vec
        return mat


class OpenAIEmbedder:
    name = "openai:text-embedding-3-small"

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.model = model

    def embed(self, texts: list[str]) -> np.ndarray:
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=json.dumps({"model": self.model, "input": texts}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            raise RuntimeError(f"OpenAI embeddings failed: {e}") from e
        vecs = [item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"])]
        mat = np.array(vecs, dtype=np.float64)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return mat / norms


def get_embedder(mock: bool | None = None):
    """Resolve the embedder from the environment (shared with the router)."""
    if mock is None:
        mock = os.environ.get("MOCK_LLM", "").lower() in {"1", "true", "yes"}
    key = os.environ.get("OPENAI_API_KEY")
    if not mock and key:
        return OpenAIEmbedder(key)
    return HashingEmbedder()


def cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rows of a vs rows of b. Inputs are assumed L2-normalized."""
    return a @ b.T

"""Embeddings for clustering, overlap_check, and (via reuse) router matching.

The router should import get_embedder() from this module so every stage of
the system embeds text identically.

Resolution (mock mode never calls a network backend):
  1. OPENAI_API_KEY  -> OpenAI text-embedding-3-small
  2. GEMINI_API_KEY  -> Gemini embedContent (GEMINI_EMBED_MODEL, default
                        gemini-embedding-001; free tier, matches the router's
                        Gemini backend so both stages embed in the same space)
  3. otherwise       -> deterministic hashing embedder, no network, no deps

NOTE: the hashing backend cannot separate same-type from different-type
pairs on the multilingual corpus (measured 2026-10-24: same-type max cosine
0.366 vs diff-type max 0.427), so a no-key mock run finds zero clusters.
Mock mode is for plumbing, not for discovery results.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
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


class GeminiEmbedder:
    """Gemini embedContent, one call per text, L2-normalized output."""

    def __init__(self, api_key: str, model: str = "gemini-embedding-001"):
        self.api_key = api_key
        self.model = model
        self.name = f"gemini:{model}"

    def _embed_one(self, text: str) -> list[float]:
        payload = {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": text}]},
        }
        delay = 2.0
        for attempt in range(4):
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return data["embedding"]["values"]
            except urllib.error.HTTPError as e:
                if (e.code == 429 or 500 <= e.code < 600) and attempt < 3:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    try:
                        wait = float(retry_after) if retry_after else delay
                    except ValueError:
                        wait = delay
                    time.sleep(min(wait, 60.0))
                    delay *= 2
                    continue
                body = e.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"Gemini embeddings error {e.code}: {body}") from e
            except urllib.error.URLError as e:
                raise RuntimeError(f"Gemini embeddings failed: {e}") from e

    def embed(self, texts: list[str]) -> np.ndarray:
        mat = np.array([self._embed_one(t) for t in texts], dtype=np.float64)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return mat / norms


def get_embedder(mock: bool | None = None):
    """Resolve the embedder from the environment (shared with the router).

    EMBED_BACKEND=gemini|openai|hash forces a backend even in mock mode, so
    an offline-LLM run can still cluster with real embeddings (hashing cannot
    separate the multilingual corpus; see module docstring).
    """
    if mock is None:
        mock = os.environ.get("MOCK_LLM", "").lower() in {"1", "true", "yes"}
    forced = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if forced == "hash":
        return HashingEmbedder()
    if forced == "openai" and os.environ.get("OPENAI_API_KEY"):
        return OpenAIEmbedder(os.environ["OPENAI_API_KEY"])
    if forced == "gemini" and os.environ.get("GEMINI_API_KEY"):
        return GeminiEmbedder(
            os.environ["GEMINI_API_KEY"],
            os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001"),
        )
    if mock:
        return HashingEmbedder()
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return OpenAIEmbedder(key)
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        return GeminiEmbedder(
            gemini_key, os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
        )
    return HashingEmbedder()


def cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rows of a vs rows of b. Inputs are assumed L2-normalized."""
    return a @ b.T

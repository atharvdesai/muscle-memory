"""Embedding provider for the router's embed_task node.

Backend selection (EMBED_BACKEND=openai|gemini|hash overrides):
  1. MOCK_LLM=1            -> local hash embedding
  2. OPENAI_API_KEY        -> openai text-embedding-3-small
  3. GEMINI_API_KEY        -> gemini embedding (GEMINI_EMBED_MODEL,
                              default gemini-embedding-001)
  4. otherwise             -> local hash embedding

The hash backend is deterministic, pure stdlib, no keys, no deps, so the
whole engine runs offline. NOTE: matching thresholds are backend-specific
(MATCH_THRESHOLD); hash-embedding cosines run lower than model embeddings.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request

DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def hash_embed(text: str, dim: int = DIM) -> list[float]:
    """Deterministic signed-hashing bag-of-words embedding, L2-normalized."""
    vec = [0.0] * dim
    for tok in _tokenize(text):
        digest = hashlib.md5(tok.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def openai_embed(text: str) -> list[float]:
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps({"model": "text-embedding-3-small", "input": text}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"openai embeddings error {e.code}: {body}") from e
    return data["data"][0]["embedding"]


def gemini_embed(text: str) -> list[float]:
    model = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent",
        data=json.dumps({
            "model": f"models/{model}",
            "content": {"parts": [{"text": text}]},
        }).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": os.environ["GEMINI_API_KEY"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"gemini embeddings error {e.code}: {body}") from e
    return data["embedding"]["values"]


class Embedder:
    """Caches embeddings in-process; the skill web is small."""

    def __init__(self) -> None:
        self._cache: dict[str, list[float]] = {}
        forced = os.environ.get("EMBED_BACKEND", "").strip().lower()
        if forced in ("openai", "gemini", "hash"):
            self.backend = forced
        elif os.environ.get("MOCK_LLM") == "1":
            self.backend = "hash"
        elif os.environ.get("OPENAI_API_KEY"):
            self.backend = "openai"
        elif os.environ.get("GEMINI_API_KEY"):
            self.backend = "gemini"
        else:
            self.backend = "hash"

    def embed(self, text: str) -> list[float]:
        key = f"{self.backend}:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"
        if key not in self._cache:
            if self.backend == "openai":
                self._cache[key] = openai_embed(text)
            elif self.backend == "gemini":
                self._cache[key] = gemini_embed(text)
            else:
                self._cache[key] = hash_embed(text)
        return self._cache[key]

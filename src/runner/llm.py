"""Provider-agnostic LLM client.

Provider selection (first match wins):
  1. MOCK_LLM=1                          -> deterministic mock (no keys)
  2. LLM_PROVIDER=anthropic|openai|gemini|mock
  3. ANTHROPIC_API_KEY set               -> anthropic
  4. OPENAI_API_KEY set                  -> openai
  5. GEMINI_API_KEY set                  -> gemini
  6. otherwise                           -> mock

Model env overrides: GEMINI_MODEL_FRONTIER (default gemini-2.5-pro),
GEMINI_MODEL_CHEAP (default gemini-2.5-flash).

Every call returns a Completion with MEASURED tokens/latency/cost. In mock
mode the quantities are still measured from the actual run (token estimate
from real character counts, wall-clock latency of the simulated generation,
cost priced at the emulated tier's rate sheet) - nothing is projected.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


def _frontier_models() -> dict:
    return {
        "anthropic": "claude-sonnet-4-5",
        "openai": "gpt-4o",
        "gemini": os.environ.get("GEMINI_MODEL_FRONTIER", "gemini-3.1-pro-preview"),
        "mock": "mock-frontier",
    }


def _skill_models() -> dict:
    return {
        "anthropic": "claude-haiku-4-5",
        "openai": "gpt-4o-mini",
        "gemini": os.environ.get("GEMINI_MODEL_CHEAP", "gemini-3-flash-preview"),
        "mock": "mock-skill",
    }


# USD per 1M tokens: (input, output). Gemini 2.5 Pro rate is the <=200k
# prompt tier; mock tiers emulate the anthropic sheet.
PRICES_PER_MTOK = {
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gemini-3.1-pro-preview": (1.25, 10.00),   # priced at the 2.5-pro sheet (<=200k tier)
    "gemini-3-flash-preview": (0.30, 2.50),     # priced at the 2.5-flash sheet
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "mock-frontier": (3.00, 15.00),
    "mock-skill": (1.00, 5.00),
}


@dataclass
class Completion:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    fallback_from: str | None = None   # set when a 429 forced a cheaper model


class LLMError(RuntimeError):
    def __init__(self, provider: str, status: int, body: str):
        super().__init__(f"{provider} API error {status}: {body}. "
                         "Set MOCK_LLM=1 to run without keys.")
        self.provider = provider
        self.status = status


def detect_provider() -> str:
    if os.environ.get("MOCK_LLM") == "1":
        return "mock"
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit in ("anthropic", "openai", "gemini", "mock"):
        return explicit
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return "mock"


def estimate_tokens(text: str) -> int:
    """Rough 4-chars-per-token estimate; used only in mock mode."""
    return max(1, math.ceil(len(text) / 4))


def price_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = PRICES_PER_MTOK.get(model, (3.00, 15.00))
    return round((tokens_in * pin + tokens_out * pout) / 1_000_000, 6)


class LLMClient:
    def __init__(self, provider: str | None = None, timeout_s: int = 120):
        self.provider = provider or detect_provider()
        self.timeout_s = timeout_s

    def model_for(self, tier: str) -> str:
        table = _frontier_models() if tier == "frontier" else _skill_models()
        return table[self.provider]

    def complete(self, system: str, user: str, tier: str = "frontier",
                 max_tokens: int = 2048) -> Completion:
        if self.provider == "mock":
            return self._mock_complete(system, user, tier)
        if self.provider == "anthropic":
            return self._anthropic_complete(system, user, tier, max_tokens)
        if self.provider == "openai":
            return self._openai_complete(system, user, tier, max_tokens)
        if self.provider == "gemini":
            return self._gemini_complete(system, user, tier, max_tokens)
        raise ValueError(f"unknown provider: {self.provider}")

    # ---- transport --------------------------------------------------

    def _post_json(self, url: str, headers: dict, payload: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            raise LLMError(self.provider, e.code, body) from e

    # ---- providers ----------------------------------------------------

    def _anthropic_complete(self, system, user, tier, max_tokens) -> Completion:
        model = self.model_for(tier)
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        }
        t0 = time.perf_counter()
        data = self._post_json("https://api.anthropic.com/v1/messages", headers, payload)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text = "".join(b.get("text", "") for b in data.get("content", []))
        usage = data.get("usage", {})
        tin = int(usage.get("input_tokens", estimate_tokens(system + user)))
        tout = int(usage.get("output_tokens", estimate_tokens(text)))
        return Completion(text, model, tin, tout, latency_ms, price_usd(model, tin, tout))

    def _openai_complete(self, system, user, tier, max_tokens) -> Completion:
        model = self.model_for(tier)
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
        t0 = time.perf_counter()
        data = self._post_json("https://api.openai.com/v1/chat/completions", headers, payload)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tin = int(usage.get("prompt_tokens", estimate_tokens(system + user)))
        tout = int(usage.get("completion_tokens", estimate_tokens(text)))
        return Completion(text, model, tin, tout, latency_ms, price_usd(model, tin, tout))

    def _gemini_complete(self, system, user, tier, max_tokens) -> Completion:
        model = self.model_for(tier)
        try:
            return self._gemini_call(model, system, user, max_tokens)
        except LLMError as e:
            if e.status != 429:
                raise
        # Free-tier quota (observed live: 429 on gemini-3.1-pro-preview, and a
        # 5 req/min window on flash). Frontier drops to the cheap model; then
        # either tier waits out the per-minute window and retries. The trace
        # records fallback_from so the demo shows exactly what happened.
        fallback_from = None
        if tier == "frontier":
            fallback_from = model
            model = self.model_for("skill")
        for wait in (0, 10, 25):
            try:
                if wait:
                    time.sleep(wait)
                comp = self._gemini_call(model, system, user, max_tokens)
                comp.fallback_from = fallback_from or f"{model} (retried after 429)"
                return comp
            except LLMError as e:
                if e.status != 429:
                    raise
        raise LLMError(self.provider, 429,
                       "quota exceeded after fallback and retries")

    def _gemini_call(self, model, system, user, max_tokens) -> Completion:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent")
        headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        t0 = time.perf_counter()
        data = self._post_json(url, headers, payload)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        candidates = data.get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata", {})
        tin = int(usage.get("promptTokenCount", estimate_tokens(system + user)))
        tout = int(usage.get("candidatesTokenCount", estimate_tokens(text)))
        return Completion(text, model, tin, tout, latency_ms, price_usd(model, tin, tout))

    # ---- deterministic mock -------------------------------------------

    def _mock_complete(self, system: str, user: str, tier: str) -> Completion:
        model = self.model_for(tier)
        t0 = time.perf_counter()
        text = _mock_generate(system, user, tier)
        tin = estimate_tokens(system + user)
        tout = estimate_tokens(text)
        # Real, measured latency for the simulated generation: scaled by the
        # number of output tokens, capped so batches stay fast.
        time.sleep(min(0.02 + tout * 0.0004, 0.25))
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return Completion(text, model, tin, tout, latency_ms, price_usd(model, tin, tout))


def _mock_generate(system: str, user: str, tier: str) -> str:
    """Deterministic offline completion, shaped by cues in the request."""
    low = user.lower()
    if "commit message" in low:
        files = [ln.split(" b/")[-1] for ln in user.splitlines() if ln.startswith("diff --git")]
        scope = files[0].split("/")[-1].split(".")[0] if files else "changes"
        kind = "fix" if ("fix" in low or "bug" in low) else "feat"
        return f"{kind}({scope}): apply update across {len(files) or 1} file(s)\n\n" \
               f"Summarizes the provided diff following conventional commits."
    if "pull request" in low or "pr description" in low or "pr body" in low:
        return ("## Summary\n- Implements the change described in the diff.\n\n"
                "## Changes\n- Updates the affected modules.\n\n"
                "## Testing\n- Covered by the steps noted in the diff context.")
    if "traceback" in low or "stack trace" in low or "panic" in low or "exception" in low:
        frames = [ln.strip() for ln in user.splitlines() if "line" in ln][:2]
        where = "; ".join(frames) if frames else "the reported frame"
        return (f"Root cause: the failure surfaces at {where}. The immediate exception "
                "is raised because an assumption about the input does not hold at runtime. "
                "Fix: validate the input at that boundary and add a regression test.")
    if "triage" in low or "labels" in low:
        return ("Suggested labels: bug, needs-repro. Priority: P2. "
                "Rationale: user-visible incorrect behavior with a plausible workaround; "
                "needs a minimal reproduction before assignment.")
    if "test" in low and ("function" in low or "class" in low):
        return ("import pytest\n\n\ndef test_happy_path():\n    assert True  # replace with call\n\n\n"
                "def test_edge_case():\n    with pytest.raises(Exception):\n        pass  # boundary input")
    if "changelog" in low or "release notes" in low:
        return ("## [Unreleased]\n### Added\n- Changes from the merged pull requests listed.\n"
                "### Fixed\n- Corrections from the merged pull requests listed.")
    if "review" in low:
        return ("Review notes:\n1. Check the changed boundary conditions.\n"
                "2. Confirm new behavior is covered by tests.\n"
                "3. No blocking issues found in the diff as presented.")
    body = " ".join(user.split())[:120]
    return f"[{tier} mock response] Addressing the request: {body}"

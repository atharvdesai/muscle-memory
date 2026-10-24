"""Provider-agnostic LLM client with a deterministic mock mode.

Configured purely by environment variables:

    ANTHROPIC_API_KEY   -> uses the Anthropic Messages API
    OPENAI_API_KEY      -> uses the OpenAI Chat Completions API
    LLM_MODEL / JUDGE_MODEL -> optional model override
    MUSCLE_MEMORY_MOCK=1    -> force mock mode even when keys exist

No SDK dependencies: both providers are called over plain HTTP (stdlib
urllib), so the whole package runs on the repo's requirements.txt alone.
Token counts come from the provider's measured `usage` field; cost is
computed from those measured counts against a static price table.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# USD per 1M tokens: (input, output). Unknown models fall back to a
# mid-range default so costs stay plausible; token counts are always the
# provider-measured ones.
_PRICES_PER_MTOK = {
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    # 3.x pricing not yet published; flash-class estimate, token counts measured.
    "gemini-3.6-flash": (0.30, 2.50),
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-3.1-pro-preview": (1.25, 10.00),
}
_DEFAULT_PRICE = (1.00, 5.00)

DEFAULT_ANTHROPIC_MODEL = "claude-3-5-haiku-20241022"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"  # integrator-verified live on the shared key 2026-10-24 (200); 2.5-flash/2.5-pro 404, 3.1-pro quota-limited


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    mock: bool = False


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = _PRICES_PER_MTOK.get(model, _DEFAULT_PRICE)
    return (tokens_in * pin + tokens_out * pout) / 1_000_000.0


class LLMClient:
    """One client, four providers: anthropic | openai | gemini | mock."""

    def __init__(self, provider: str | None = None, model: str | None = None,
                 timeout_s: int = 60):
        forced_mock = os.getenv("MUSCLE_MEMORY_MOCK", "").lower() in ("1", "true", "yes")
        if provider is None:
            if forced_mock:
                provider = "mock"
            elif os.getenv("ANTHROPIC_API_KEY"):
                provider = "anthropic"
            elif os.getenv("OPENAI_API_KEY"):
                provider = "openai"
            elif os.getenv("GEMINI_API_KEY"):
                provider = "gemini"
            else:
                provider = "mock"
        self.provider = provider
        self.timeout_s = timeout_s
        if self.provider == "anthropic":
            self.model = model or os.getenv("JUDGE_MODEL") or os.getenv("LLM_MODEL") \
                or DEFAULT_ANTHROPIC_MODEL
            self._key = os.environ["ANTHROPIC_API_KEY"]
        elif self.provider == "openai":
            self.model = model or os.getenv("JUDGE_MODEL") or os.getenv("LLM_MODEL") \
                or DEFAULT_OPENAI_MODEL
            self._key = os.environ["OPENAI_API_KEY"]
        elif self.provider == "gemini":
            self.model = model or os.getenv("JUDGE_MODEL") or os.getenv("LLM_MODEL") \
                or os.getenv("GEMINI_MODEL_CHEAP") or DEFAULT_GEMINI_MODEL
            self._key = os.environ["GEMINI_API_KEY"]
        else:
            self.provider = "mock"
            self.model = model or "mock-deterministic"
            self._key = ""

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    def complete(self, system: str, user: str, max_tokens: int = 1024,
                 temperature: float = 0.0) -> LLMResponse:
        if self.is_mock:
            # Deterministic stand-in: a fixed-shape reply so callers exercising
            # the plumbing get stable output. The judge never scores through
            # this path - mock mode uses the heuristic judge instead.
            return LLMResponse(
                text="MOCK",
                provider="mock",
                model=self.model,
                tokens_in=(len(system) + len(user)) // 4,
                tokens_out=1,
                latency_ms=1,
                cost_usd=0.0,
                mock=True,
            )
        t0 = time.monotonic()
        if self.provider == "anthropic":
            text, tin, tout = self._anthropic(system, user, max_tokens, temperature)
        elif self.provider == "openai":
            text, tin, tout = self._openai(system, user, max_tokens, temperature)
        else:
            text, tin, tout = self._gemini(system, user, max_tokens, temperature)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            tokens_in=tin,
            tokens_out=tout,
            latency_ms=latency_ms,
            cost_usd=cost_usd(self.model, tin, tout),
        )

    def _post(self, url: str, headers: dict, payload: dict) -> dict:
        body_bytes = json.dumps(payload).encode("utf-8")
        delay = 5.0
        last_err: Exception | None = None
        for attempt in range(5):
            req = urllib.request.Request(
                url, data=body_bytes, method="POST",
                headers={"Content-Type": "application/json", **headers})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:500]
                # 429 (rate limit) and 5xx (transient) get exponential backoff;
                # free-tier Gemini is 5 req/min, so waits matter here.
                if e.code == 429 or 500 <= e.code < 600:
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    try:
                        wait = float(retry_after) if retry_after else delay
                    except ValueError:
                        wait = delay
                    wait += random.uniform(0, 2.0)
                    last_err = RuntimeError(f"{self.provider} API error {e.code}: {body}")
                    if attempt < 4:
                        time.sleep(wait)
                        delay = min(delay * 2, 45.0)
                        continue
                raise RuntimeError(f"{self.provider} API error {e.code}: {body}") from e
            except (TimeoutError, urllib.error.URLError) as e:
                last_err = e
                if attempt < 4:
                    time.sleep(delay)
                    delay = min(delay * 2, 45.0)
                    continue
                raise
        raise RuntimeError(f"{self.provider} API retries exhausted: {last_err}")

    def _anthropic(self, system: str, user: str, max_tokens: int,
                   temperature: float) -> tuple[str, int, int]:
        data = self._post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self._key, "anthropic-version": "2023-06-01"},
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        text = "".join(b.get("text", "") for b in data.get("content", []))
        usage = data.get("usage", {})
        return text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))

    def _openai(self, system: str, user: str, max_tokens: int,
                temperature: float) -> tuple[str, int, int]:
        data = self._post(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {self._key}"},
            {
                "model": self.model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))

    def _gemini(self, system: str, user: str, max_tokens: int,
                temperature: float) -> tuple[str, int, int]:
        data = self._post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            {"x-goog-api-key": self._key},
            {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": temperature,
                                     "maxOutputTokens": max(max_tokens, 4096),
                                     # panel verdicts are strict JSON; disable
                                     # thinking so it can't eat the budget
                                     "thinkingConfig": {"thinkingBudget": 0}},
            },
        )
        parts = data["candidates"][0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata", {})
        return text, int(usage.get("promptTokenCount", 0)), int(usage.get("candidatesTokenCount", 0))

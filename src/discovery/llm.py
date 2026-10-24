"""Provider-agnostic LLM client with a deterministic mock mode.

Resolution order (unless mock is forced):
  1. GEMINI_API_KEY set    -> Gemini generateContent API (stdlib urllib, no new deps);
                              model from GEMINI_MODEL_CHEAP (default gemini-3-flash-preview)
  2. ANTHROPIC_API_KEY set -> Anthropic Messages API
  3. OPENAI_API_KEY set    -> OpenAI Chat Completions API
  4. none                  -> MockLLM (fully deterministic, no network)

Override the model with MUSCLE_MEMORY_MODEL. Force the mock with MOCK_LLM=1.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter

# Marker the proposal prompt carries so the mock can recognize it.
PROPOSE_MARKER = "SKILL-CANDIDATE-REQUEST"


class LLMError(RuntimeError):
    pass


class BaseLLM:
    name = "base"

    def complete(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        raise NotImplementedError


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"{url} -> HTTP {e.code}: {e.read()[:500]!r}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"{url} -> {e}") from e


class AnthropicLLM(BaseLLM):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        return "".join(
            block.get("text", "") for block in data.get("content", [])
        )


class OpenAILLM(BaseLLM):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {self.api_key}"},
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        return data["choices"][0]["message"]["content"]


class GeminiLLM(BaseLLM):
    """Google Gemini via the generateContent REST API (stdlib urllib)."""

    name = "gemini"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        data = _post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            {"x-goog-api-key": self.api_key},
            {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
        )
        parts = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        return "".join(p.get("text", "") for p in parts)


class MockLLM(BaseLLM):
    """Deterministic offline LLM. Lets the whole pipeline run with no keys.

    For skill-candidate requests it synthesizes a stable, plausible skill spec
    from the cluster samples themselves (keyword extraction, no randomness).
    """

    name = "mock"

    _STOPWORDS = {
        "a", "an", "the", "this", "that", "these", "those", "for", "to", "of",
        "in", "on", "and", "or", "with", "is", "are", "be", "it", "its", "as",
        "from", "into", "by", "at", "write", "please", "task", "context",
        "prompt", "sample", "me", "my", "your", "you", "given", "following",
        "text", "here", "below", "output", "input", "use", "using",
    }

    def complete(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        if PROPOSE_MARKER in user:
            return self._propose_json(user)
        # Generic deterministic completion: extractive echo of the request.
        digest = hashlib.md5((system + user).encode()).hexdigest()[:8]
        snippet = re.sub(r"\s+", " ", user).strip()[:400]
        return f"[mock-completion {digest}] {snippet}"

    def _keywords(self, text: str, n: int = 4) -> list[str]:
        words = re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
        common = [
            w
            for w, _ in Counter(
                w for w in words if w not in self._STOPWORDS
            ).most_common(20)
        ]
        return common[:n] or ["repeated", "workflow"]

    def _propose_json(self, user: str) -> str:
        kws = self._keywords(user)
        slug = "-".join(kws[:3])
        seed = int(hashlib.md5(user.encode()).hexdigest()[:8], 16)
        samples = re.findall(r"SAMPLE \d+ PROMPT: (.+)", user)
        first = samples[0][:120] if samples else "the repeated task"
        name = " ".join(w.capitalize() for w in kws[:3])
        candidate = {
            "name": name,
            "responsibility": (
                f"Handle the repeated '{first.strip()}' style of task: "
                f"produce the expected {kws[0]} output from the given input"
            ),
            "trigger_description": (
                f"Task asks for {kws[0]} work resembling: {first.strip()}"
            ),
            "parts": [
                {
                    "id": "p1",
                    "kind": "instructions",
                    "content": (
                        "Follow this procedure exactly and stop.\n"
                        f"1. Read the task prompt and extract the {kws[0]} inputs.\n"
                        f"2. Apply the standard {kws[0]} transformation observed "
                        f"across the {len(samples)} historical examples.\n"
                        "3. Emit only the final output, no preamble."
                    ),
                }
            ],
        }
        if seed % 2 == 0 and len(kws) > 1:
            candidate["parts"].append(
                {
                    "id": "p2",
                    "kind": "script",
                    "content": (
                        "# deterministic helper for the " + slug + " skill\n"
                        "def normalize(text: str) -> str:\n"
                        "    return ' '.join(text.split())\n"
                    ),
                }
            )
        return json.dumps(candidate)


def get_llm(mock: bool | None = None) -> BaseLLM:
    """Resolve the LLM client from the environment."""
    if mock is None:
        mock = os.environ.get("MOCK_LLM", "").lower() in {"1", "true", "yes"}
    gemini_key = os.environ.get("GEMINI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if mock or (not gemini_key and not anthropic_key and not openai_key):
        return MockLLM()
    model_override = os.environ.get("MUSCLE_MEMORY_MODEL")
    if gemini_key:
        return GeminiLLM(
            gemini_key,
            model_override
            or os.environ.get("GEMINI_MODEL_CHEAP", "gemini-3-flash-preview"),
        )
    if anthropic_key:
        return AnthropicLLM(anthropic_key, model_override or "claude-sonnet-4-5")
    return OpenAILLM(openai_key, model_override or "gpt-4o-mini")

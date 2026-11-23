"""LLM layer: Gemini over plain HTTPS (httpx), with retry and backoff.

The rest of the agent only uses LLMProvider.generate(), so swapping to Claude,
Groq or Ollama later means writing one new class here. The API key is read from
the environment and is never printed or logged.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv

load_dotenv()  # finds the .env in the project root

RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMUnavailable(Exception):
    """Gemini is down, rate-limited past our retries, or not configured.
    The agent catches this and switches to the scripted fallback."""


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class LLMReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model_content: dict[str, Any] = field(default_factory=dict)  # append to history as-is


class LLMProvider(Protocol):
    def generate(
        self,
        system: str,
        contents: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMReply: ...


# ---- helpers to build conversation history (Gemini format) ----

def user_message(text: str) -> dict[str, Any]:
    return {"role": "user", "parts": [{"text": text}]}


def tool_results_message(results: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    return {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": name, "response": {"result": result}}}
            for name, result in results
        ],
    }


class GeminiProvider:
    def __init__(self, model: str | None = None, max_attempts: int = 4) -> None:
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.max_attempts = max_attempts

    @staticmethod
    def has_key() -> bool:
        return bool(os.getenv("GEMINI_API_KEY", "").strip())

    def generate(self, system, contents, tools) -> LLMReply:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise LLMUnavailable("GEMINI_API_KEY is not set in .env")

        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.3},
        }
        if tools:
            body["tools"] = [{"functionDeclarations": tools}]

        models = [self.model]
        fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash").strip()
        if fallback and fallback != self.model:
            models.append(fallback)

        last_error = "unknown error"
        for mi, model in enumerate(models):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            timeout = float(os.getenv("GEMINI_TIMEOUT", "25")) if mi == 0 else 45.0
            for attempt in range(2):
                try:
                    resp = httpx.post(url, json=body, headers={"x-goog-api-key": key}, timeout=timeout)
                except httpx.TimeoutException:
                    last_error = f"{model}: timeout"
                    break  # slow model: switch now, do not wait again
                except httpx.HTTPError as exc:
                    last_error = f"{model}: network error {type(exc).__name__}"
                else:
                    if resp.status_code == 200:
                        self.last_model = model
                        return self._parse(resp.json())
                    last_error = f"{model}: HTTP {resp.status_code} {resp.text[:120]}"
                    if resp.status_code == 404:
                        break  # model retired: try the next one
                    if resp.status_code not in RETRY_STATUS:
                        raise LLMUnavailable(last_error)
                if attempt == 0:
                    time.sleep(1 + random.random())
        raise LLMUnavailable(f"Gemini failed on all models ({last_error})")

    @staticmethod
    def _parse(data: dict[str, Any]) -> LLMReply:
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMUnavailable("Gemini returned no candidates (possibly blocked)")
        content = candidates[0].get("content") or {"role": "model", "parts": []}
        content.setdefault("role", "model")
        texts: list[str] = []
        calls: list[ToolCall] = []
        for part in content.get("parts", []):
            if "text" in part:
                texts.append(part["text"])
            if "functionCall" in part:
                fc = part["functionCall"]
                calls.append(ToolCall(name=fc["name"], args=dict(fc.get("args") or {})))
        return LLMReply(text="".join(texts).strip(), tool_calls=calls, model_content=content)


def get_llm() -> LLMProvider:
    """Pick the LLM from config. Only Gemini for now; add others here."""
    name = os.getenv("LLM_PROVIDER", "gemini").lower()
    if name == "gemini":
        return GeminiProvider()
    raise ValueError(f"Unknown LLM_PROVIDER '{name}'")
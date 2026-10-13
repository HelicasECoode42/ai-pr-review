from __future__ import annotations

import json
import time
from typing import Protocol

import httpx


class ProviderError(Exception):
    """Raised when the model provider fails."""

    def __init__(
        self, message: str, *, status_code: int | None = None, kind: str = "provider_error"
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = "http_error" if status_code is not None else kind


class ReviewModelProvider(Protocol):
    def complete_json(self, system_prompt: str, user_prompt: str) -> str: ...


class OpenAICompatibleProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 45.0,
        stream: bool = False,
        max_output_tokens: int | None = None,
        total_timeout: float | None = None,
    ) -> None:
        self._model = model
        self._stream = stream
        self._max_output_tokens = max_output_tokens
        self._total_timeout = total_timeout
        self.usage_records: list[dict] = []
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        if self._max_output_tokens is not None:
            payload["max_tokens"] = self._max_output_tokens
        try:
            if self._stream:
                return self._complete_stream(payload)
            response = self._client.post("/chat/completions", json=payload)
            # Some OpenAI-compatible providers accept the chat API but reject
            # response_format. Retry once without it so the downstream JSON
            # parser can still validate the model output.
            if response.status_code == 400 and _rejects_response_format(response):
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format")
                response = self._client.post("/chat/completions", json=fallback_payload)
            response.raise_for_status()
        except httpx.TimeoutException:
            raise ProviderError("AI model request timed out", kind="timeout") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = _safe_error_detail(exc.response)
            if status == 401:
                raise ProviderError(
                    f"Authentication failed: invalid or expired API key{detail}",
                    status_code=status,
                ) from exc
            if status == 429:
                raise ProviderError(
                    f"Rate limited by model provider; try again later{detail}",
                    status_code=status,
                ) from exc
            if status >= 500:
                raise ProviderError(
                    f"Model provider server error (HTTP {status}){detail}",
                    status_code=status,
                ) from exc
            raise ProviderError(
                f"Model provider returned HTTP {status}{detail}",
                status_code=status,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Failed to reach model provider: {exc}") from exc

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Content is not text")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Malformed model response", kind="malformed_response") from exc
        self.usage_records.append(
            {
                "model": data.get("model", self._model),
                "usage": data.get("usage"),
                "request_id": data.get("id"),
            }
        )
        return content

    def _complete_stream(self, payload: dict) -> str:
        started = time.perf_counter()
        payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        # SSE keeps the connection active while the model reasons. It changes
        # transport only; model, temperature and review prompts stay identical.
        for attempt in range(2):
            with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    response.read()
                    if (
                        attempt == 0
                        and response.status_code == 400
                        and _rejects_response_format(response)
                    ):
                        payload.pop("response_format", None)
                        continue
                    response.raise_for_status()
                content = []
                usage = None
                model = self._model
                request_id = None
                done = False
                exhausted = False
                total = 0
                finish_reason = None
                for line in response.iter_lines():
                    if self._total_timeout and time.perf_counter() - started > self._total_timeout:
                        raise ProviderError(
                            "Model call exceeded total time budget", kind="budget_exhausted"
                        )
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        done = True
                        break
                    try:
                        event = json.loads(data)
                        model = event.get("model", model)
                        request_id = event.get("id", request_id)
                        if event.get("usage") is not None:
                            usage = event["usage"]
                        for choice in event.get("choices", []):
                            finish_reason = choice.get("finish_reason") or finish_reason
                            if choice.get("finish_reason") == "length":
                                exhausted = True
                            text = choice.get("delta", {}).get("content")
                            if text:
                                if not isinstance(text, str):
                                    raise ValueError("Non-text content")
                                content.append(text)
                                total += len(text)
                                if total > 2_000_000:
                                    raise ProviderError(
                                        "Model output exceeded bounded response size"
                                    )
                    except (ValueError, AttributeError, TypeError) as exc:
                        raise ProviderError("Malformed streaming model response") from exc
                self.usage_records.append(
                    {
                        "model": model,
                        "usage": usage,
                        "request_id": request_id,
                        "finish_reason": finish_reason,
                        "stream_complete": done,
                    }
                )
                if exhausted:
                    raise ProviderError(
                        "Model exhausted output token budget", kind="budget_exhausted"
                    )
                if not done or not content:
                    raise ProviderError(
                        "Incomplete streaming model response", kind="incomplete_response"
                    )
                return "".join(content)
        raise ProviderError("Model rejected structured response format")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAICompatibleProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _safe_error_detail(response: httpx.Response) -> str:
    """Return a short, secret-safe provider error detail for diagnostics."""
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("type")
                if message:
                    return f": {str(message)[:240]}"
            message = body.get("message")
            if message:
                return f": {str(message)[:240]}"
    except (ValueError, TypeError):
        pass
    return ""


def _rejects_response_format(response: httpx.Response) -> bool:
    """Detect the common 400 response_format incompatibility case."""
    text = response.text.lower()
    return "response_format" in text or "json_object" in text or "structured output" in text

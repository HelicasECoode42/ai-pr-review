from __future__ import annotations

from typing import Protocol

import httpx


class ProviderError(Exception):
    """Raised when the model provider fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ReviewModelProvider(Protocol):
    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        ...


class OpenAICompatibleProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 45.0,
    ) -> None:
        self._model = model
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
        try:
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
            raise ProviderError("AI model request timed out") from None
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
            raise ProviderError(
                f"Failed to reach model provider: {exc}"
            ) from exc

        data = response.json()
        return data["choices"][0]["message"]["content"]

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

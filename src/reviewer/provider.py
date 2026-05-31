from __future__ import annotations

from typing import Protocol

import httpx


class ProviderError(Exception):
    """Raised when the model provider fails."""


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
        try:
            response = self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            raise ProviderError("AI model request timed out") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise ProviderError(
                    "Authentication failed: invalid or expired API key"
                ) from exc
            if status == 429:
                raise ProviderError(
                    "Rate limited by model provider; try again later"
                ) from exc
            if status >= 500:
                raise ProviderError(
                    f"Model provider server error (HTTP {status})"
                ) from exc
            raise ProviderError(
                f"Model provider returned HTTP {status}"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Failed to reach model provider: {exc}"
            ) from exc

        data = response.json()
        return data["choices"][0]["message"]["content"]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpenAICompatibleProvider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

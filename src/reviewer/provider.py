from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Protocol

import httpx


class ProviderError(Exception):
    """Raised when the model provider fails."""

    def __init__(
        self, message: str, *, status_code: int | None = None, kind: str = "provider_error"
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = (
            ("http_error" if status_code is not None else kind)
            if kind == "provider_error"
            else kind
        )


class ReviewModelProvider(Protocol):
    def complete_json(self, system_prompt: str, user_prompt: str) -> str: ...


class ReviewRequestBudget:
    """Shared per-review guard for estimated input, attempts, and wall time.

    Input uses a conservative UTF-8 byte upper bound plus message framing. It is
    a send guard, not exact tokenization or provider billing telemetry.
    """

    ESTIMATOR = "json-utf8-byte-upper-bound+512-envelope-v1"

    def __init__(
        self,
        max_input_token_upper_bound: int | None,
        max_http_attempts: int,
        total_timeout_seconds: float,
    ) -> None:
        self.max_input_token_upper_bound = max_input_token_upper_bound
        self.max_http_attempts = max_http_attempts
        self.total_timeout_seconds = total_timeout_seconds
        self.started_at = time.monotonic()
        self.http_attempts = 0
        self.input_upper_bound = 0
        self.blocked_reason: str | None = None
        self._lock = threading.Lock()

    def reserve(self, system_prompt: str, user_prompt: str) -> float:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        # JSON encoding accounts for escaping; the extra envelope allowance
        # covers model/options and top-level fields without claiming a tokenizer.
        estimate = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 512
        with self._lock:
            remaining = self.total_timeout_seconds - (time.monotonic() - self.started_at)
            if remaining <= 0:
                self.blocked_reason = "review_deadline_exceeded"
                raise ProviderError("Review time budget exhausted", kind=self.blocked_reason)
            if self.http_attempts >= self.max_http_attempts:
                self.blocked_reason = "http_attempt_budget_exhausted"
                raise ProviderError(
                    "Review HTTP attempt budget exhausted", kind=self.blocked_reason
                )
            if (self.max_input_token_upper_bound is not None
                    and self.input_upper_bound + estimate > self.max_input_token_upper_bound):
                self.blocked_reason = "input_budget_exhausted"
                raise ProviderError(
                    "Review input upper-bound budget exhausted", kind=self.blocked_reason
                )
            self.http_attempts += 1
            self.input_upper_bound += estimate
            return remaining

    def check_deadline(self) -> None:
        with self._lock:
            if time.monotonic() - self.started_at >= self.total_timeout_seconds:
                self.blocked_reason = "review_deadline_exceeded"
                raise ProviderError("Review time budget exhausted", kind=self.blocked_reason)

    def snapshot(self) -> dict[str, int | float | str]:
        with self._lock:
            return {
                "request_http_attempts": self.http_attempts,
                "request_http_attempt_cap": self.max_http_attempts,
                "request_input_upper_bound_utf8_bytes": self.input_upper_bound,
                "request_input_upper_bound_cap_utf8_bytes": (
                    self.max_input_token_upper_bound
                    if self.max_input_token_upper_bound is not None else "unbounded"
                ),
                "request_input_upper_bound_unit": "utf8_bytes",
                "request_input_estimated_method": self.ESTIMATOR,
                "request_budget_stop_reason": self.blocked_reason or "none",
                "request_budget_elapsed_seconds": round(time.monotonic() - self.started_at, 3),
            }


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
        reasoning_effort: str | None = None,
        request_attempt_hook: Callable[[], None] | None = None,
        request_budget: ReviewRequestBudget | None = None,
        read_timeout: float | None = None,
        connect_timeout: float = 10.0,
    ) -> None:
        self._model = model
        self._stream = stream
        self._max_output_tokens = max_output_tokens
        self._total_timeout = total_timeout
        self._reasoning_effort = reasoning_effort
        self._request_attempt_hook = request_attempt_hook
        self._request_budget = request_budget
        self._read_timeout = read_timeout if read_timeout is not None else timeout
        self._connect_timeout = connect_timeout
        self._timeout = timeout
        self.http_attempts = 0
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
        if self._reasoning_effort is not None:
            payload["reasoning_effort"] = self._reasoning_effort
        if self._max_output_tokens is not None:
            payload["max_tokens"] = self._max_output_tokens
        try:
            if self._stream:
                return self._complete_stream(payload, system_prompt, user_prompt)
            remaining = self._begin_http_attempt(system_prompt, user_prompt)
            response = self._post_with_deadline(payload, remaining)
            self._raise_deadline_if_elapsed()
            # Some OpenAI-compatible providers accept the chat API but reject
            # response_format. Retry once without it so the downstream JSON
            # parser can still validate the model output.
            if response.status_code == 400 and _rejects_response_format(response):
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format")
                remaining = self._begin_http_attempt(system_prompt, user_prompt)
                response = self._post_with_deadline(fallback_payload, remaining)
                self._raise_deadline_if_elapsed()
            response.raise_for_status()
        except httpx.ConnectTimeout:
            self._raise_deadline_if_elapsed()
            raise ProviderError("AI model connection timed out", kind="connect_timeout") from None
        except httpx.ReadTimeout:
            self._raise_deadline_if_elapsed()
            raise ProviderError("AI model response read timed out", kind="read_timeout") from None
        except httpx.WriteTimeout:
            self._raise_deadline_if_elapsed()
            raise ProviderError("AI model request write timed out", kind="write_timeout") from None
        except httpx.PoolTimeout:
            self._raise_deadline_if_elapsed()
            raise ProviderError("AI model connection pool timed out", kind="pool_timeout") from None
        except httpx.TimeoutException:
            self._raise_deadline_if_elapsed()
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
            self._raise_deadline_if_elapsed()
            raise ProviderError(f"Failed to reach model provider: {exc}") from exc

        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Content is not text")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Malformed model response", kind="malformed_response") from exc
        self.usage_records.append(
            {
                "model": data.get("model", self._model),
                "usage": data.get("usage"),
                "request_id": data.get("id"),
                "finish_reason": choice.get("finish_reason"),
            }
        )
        if choice.get("finish_reason") == "length":
            raise ProviderError(
                "Model exhausted output token budget (truncated at token limit)",
                kind="output_truncated",
            )
        return content

    def _complete_stream(self, payload: dict, system_prompt: str, user_prompt: str) -> str:
        started = time.perf_counter()
        payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        # SSE changes transport only. The timer closes a slow-dripping stream at
        # the shared review deadline even when no next line arrives to inspect.
        for attempt in range(2):
            remaining = self._begin_http_attempt(system_prompt, user_prompt)
            with (
                self._request_deadline(remaining),
                self._client.stream(
                    "POST", "/chat/completions", json=payload,
                    timeout=self._request_timeout(remaining),
                ) as response,
            ):
                    if response.status_code >= 400:
                        response.read()
                        if (attempt == 0 and response.status_code == 400
                                and _rejects_response_format(response)):
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
                        if (self._total_timeout
                                and time.perf_counter() - started > self._total_timeout):
                            raise ProviderError(
                                "Model call exceeded total time budget", kind="total_timeout"
                            )
                        if self._request_budget is not None:
                            self._request_budget.check_deadline()
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
                            "Model exhausted output token budget (truncated at token limit)",
                            kind="output_truncated",
                        )
                    if not done or not content:
                        raise ProviderError(
                            "Incomplete streaming model response", kind="incomplete_response"
                        )
                    return "".join(content)
        raise ProviderError("Model rejected structured response format")

    def _begin_http_attempt(self, system_prompt: str = "", user_prompt: str = "") -> float:
        if self._request_attempt_hook is not None:
            self._request_attempt_hook()
        if self._request_budget is not None:
            remaining = self._request_budget.reserve(system_prompt, user_prompt)
        else:
            remaining = self._total_timeout or self._timeout
        self.http_attempts += 1
        return remaining

    def _post_with_deadline(self, payload: dict, remaining: float) -> httpx.Response:
        with self._request_deadline(remaining):
            return self._client.post(
                "/chat/completions", json=payload,
                timeout=self._request_timeout(remaining),
            )

    @contextmanager
    def _request_deadline(self, remaining: float):
        """Close this review's client at its shared wall deadline, even on slow-drip I/O."""
        if self._request_budget is None:
            yield
            return
        timer = threading.Timer(max(0.001, remaining), self._client.close)
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()

    def _raise_deadline_if_elapsed(self) -> None:
        if self._request_budget is not None:
            self._request_budget.check_deadline()

    def _request_timeout(self, remaining: float) -> httpx.Timeout:
        return httpx.Timeout(
            connect=min(self._connect_timeout, remaining),
            read=min(self._read_timeout, remaining),
            write=min(self._connect_timeout, remaining),
            pool=min(self._connect_timeout, remaining),
        )

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

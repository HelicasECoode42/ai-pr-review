from __future__ import annotations

import httpx
import pytest
import respx

from src.reviewer.provider import OpenAICompatibleProvider, ProviderError


@respx.mock
def test_provider_retries_without_response_format_for_compatible_api() -> None:
    route = respx.post("https://provider.test/v1/chat/completions")
    route.side_effect = [
        httpx.Response(
            400,
            json={"error": {"message": "response_format is not supported"}},
        ),
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        ),
    ]

    with OpenAICompatibleProvider(
        api_key="test-key",
        model="test-model",
        base_url="https://provider.test/v1",
    ) as provider:
        assert provider.complete_json("system", "user") == '{"ok": true}'

    assert route.call_count == 2
    assert "response_format" in route.calls[0].request.content.decode()
    assert "response_format" not in route.calls[1].request.content.decode()


@respx.mock
def test_provider_preserves_safe_http_error_detail() -> None:
    route = respx.post("https://provider.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "model does not exist"}},
        )
    )

    with (
        OpenAICompatibleProvider(
            api_key="test-key",
            model="missing-model",
            base_url="https://provider.test/v1",
        ) as provider,
        pytest.raises(ProviderError, match="model does not exist") as exc_info,
    ):
        provider.complete_json("system", "user")

    assert exc_info.value.status_code == 400
    assert route.call_count == 1


@respx.mock
def test_streaming_provider_collects_content_and_actual_usage() -> None:
    events = (
        'data: {"id":"req","model":"served-model",'
        '"choices":[{"delta":{"content":"{\\"ok\\":"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"true}"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":8,'
        '"completion_tokens":4,"total_tokens":12}}\n\n'
        "data: [DONE]\n\n"
    )
    route = respx.post("https://provider.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=events)
    )
    with OpenAICompatibleProvider(
        "test-key", "alias", "https://provider.test/v1", stream=True
    ) as provider:
        assert provider.complete_json("system", "user") == '{"ok":true}'
        assert provider.usage_records[0]["model"] == "served-model"
        assert provider.usage_records[0]["usage"]["total_tokens"] == 12
    assert b'"stream":true' in route.calls[0].request.content


@respx.mock
def test_streaming_provider_rejects_unfinished_content() -> None:
    respx.post("https://provider.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text='data: {"choices":[{"delta":{"content":"{}"}}]}\n\n')
    )
    with OpenAICompatibleProvider(
        "key", "model", "https://provider.test/v1", stream=True
    ) as provider:
        with pytest.raises(ProviderError, match="Incomplete streaming"):
            provider.complete_json("system", "user")
        assert provider.usage_records[0]["stream_complete"] is False
        assert provider.usage_records[0]["usage"] is None


@respx.mock
def test_output_budget_exhaustion_is_failure_not_repaired_success() -> None:
    respx.post("https://provider.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"length"}]}\n\n'
            'data: {"choices":[],"usage":{"total_tokens":10}}\n\ndata: [DONE]\n\n',
        )
    )
    with OpenAICompatibleProvider(
        "key", "model", "https://provider.test/v1", stream=True, max_output_tokens=4
    ) as provider:
        with pytest.raises(ProviderError, match="output token budget"):
            provider.complete_json("system", "user")
        assert provider.usage_records[0]["usage"]["total_tokens"] == 10

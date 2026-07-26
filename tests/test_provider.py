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

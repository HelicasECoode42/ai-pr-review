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
def test_request_attempt_hook_counts_fallback_sends_and_denies_before_send() -> None:
    route = respx.post("https://provider.test/v1/chat/completions")
    route.side_effect = [
        httpx.Response(400, json={"error": {"message": "response_format is not supported"}}),
        httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}),
    ]
    started = []

    def allow_one_send():
        if started:
            raise ProviderError("request cap", kind="request_budget_exhausted")
        started.append(True)

    with OpenAICompatibleProvider(
        "test-key", "test-model", "https://provider.test/v1",
        request_attempt_hook=allow_one_send,
    ) as provider:
        with pytest.raises(ProviderError) as exc_info:
            provider.complete_json("private system text", "private request text")
        assert exc_info.value.kind == "request_budget_exhausted"
        assert provider.http_attempts == 1

    # The fallback was denied at the hook and never reached the transport.
    assert route.call_count == 1
    assert "private request text" not in str(exc_info.value)


@respx.mock
def test_non_streaming_length_finish_reason_is_classified_and_usage_retained() -> None:
    respx.post("https://provider.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": '{"partial":'}, "finish_reason": "length"}],
            "usage": {"total_tokens": 11},
        })
    )
    with OpenAICompatibleProvider("key", "model", "https://provider.test/v1") as provider:
        with pytest.raises(ProviderError, match="output token budget") as exc_info:
            provider.complete_json("system", "user")
        assert exc_info.value.kind == "output_truncated"
        assert provider.usage_records[0]["usage"]["total_tokens"] == 11
        assert provider.usage_records[0]["finish_reason"] == "length"


def test_usage_completeness_reports_known_and_unknown_attempts():
    from src.evaluation.paired import _usage_status

    records = [
        {"usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}},
        {"usage": {"total_tokens": 2}},
    ]
    status = _usage_status(records, 3)
    assert status == {
        "known_usage_records": 1,
        "unknown_http_attempts": 2,
        "complete_for_attempts": False,
    }
    assert records[1]["usage"] == {"total_tokens": 2}


def test_empty_or_partial_usage_is_not_treated_as_known():
    from src.evaluation.paired import _usage_status

    for usage in ({}, {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3}):
        status = _usage_status([{"usage": usage}], 1)
        assert status == {
            "known_usage_records": 0,
            "unknown_http_attempts": 1,
            "complete_for_attempts": False,
        }


def test_read_timeout_has_specific_safe_kind():
    def timeout_request(request):
        raise httpx.ReadTimeout("read timeout", request=request)

    transport = httpx.MockTransport(timeout_request)
    provider = OpenAICompatibleProvider("key", "model", "https://provider.test/v1")
    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://provider.test/v1", headers={"Authorization": "Bearer key"},
        transport=transport,
    )
    with provider, pytest.raises(ProviderError) as exc_info:
        provider.complete_json("system", "user")
    assert exc_info.value.kind == "read_timeout"
    assert "Bearer" not in str(exc_info.value)


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


@respx.mock
def test_explicit_reasoning_effort_is_sent_without_changing_model():
    import json
    route = respx.post("https://provider.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
    )
    with OpenAICompatibleProvider("test-key", "deepseek-v4-flash", "https://provider.test/v1",
                                  reasoning_effort="low") as provider:
        provider.complete_json("Return JSON", "Check")
    body = json.loads(route.calls[0].request.content)
    assert body["reasoning_effort"] == "low"
    assert body["model"] == "deepseek-v4-flash"

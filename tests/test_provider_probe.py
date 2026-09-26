"""Provider checks must exercise the same saved request settings as real chat."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import module_stubs  # noqa: F401
from config import normalize_provider_config
from endpoint_adapters import EndpointAdapterError
import endpoint_adapters
import providers


def normalized(**edits):
    return normalize_provider_config({"name": "Fixture", "url": "https://example.com/v1",
        "model": "chosen-model", "requires_key": False, "temperature": 0, **edits})


def test_normalization_preserves_key_precedence_aliases_and_zero_temperature():
    with patch.dict("os.environ", {"FIXTURE_PROVIDER_KEY": "env-fixture-key"}):
        cfg = normalized(url=None, base_url="https://example.com/api", api_key="direct-fixture-key",
                         key_env="FIXTURE_PROVIDER_KEY", endpoint="gemini", effort="high")
    assert cfg["url"] == "https://example.com/api"
    assert cfg["key"] == "direct-fixture-key"
    assert cfg["temperature"] == 0
    assert cfg["endpoint_type"] == "gemini"
    assert cfg["reasoning_effort"] == "high"


@pytest.mark.parametrize("endpoint,expected", [
    ("gemini", {"generationConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}}}),
    ("openai-responses", {"reasoning": {"effort": "high"}}),
    ("openai-chat", {"reasoning_effort": "high"}),
])
def test_reasoning_uses_the_selected_endpoint(endpoint, expected):
    cfg = normalized(endpoint_type=endpoint, reasoning_effort="high")
    assert providers.build_reasoning_extra_body(cfg) == expected


def test_explicit_reasoning_format_and_custom_body_remain_overrides():
    cfg = normalized(endpoint_type="gemini", reasoning_effort="high",
                     extra_body={"generationConfig": {"thinkingConfig": {"thinkingLevel": "LOW"}}})
    original = deepcopy(cfg)
    assert providers.build_reasoning_extra_body(cfg)["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"
    assert cfg == original
    cfg["reasoning_format"] = "openai_chat"
    assert providers.build_reasoning_extra_body(cfg)["reasoning_effort"] == "high"


def test_gemini_probe_sends_native_reasoning_and_retains_sampling_and_overrides():
    cfg = normalized(endpoint_type="gemini", model="gemini-3.8-flash", reasoning_effort="high",
                     max_tokens=2048, include_body="generationConfig:\n  topP: 0.7",
                     include_headers="X-Fixture: value")
    post = AsyncMock(return_value={"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": "private reasoning", "thought": True}, {"text": "Hello."}]}}]})
    with patch.object(endpoint_adapters, "post_json_request", post):
        result = asyncio.run(providers.probe_provider(cfg, timeout=4))
    post.assert_awaited_once()
    body = post.await_args.args[2]
    assert "reasoning_effort" not in body
    assert body["generationConfig"] == {"temperature": 0, "maxOutputTokens": 2048,
        "topP": 0.7, "thinkingConfig": {"thinkingLevel": "HIGH"}}
    assert post.await_args.args[1]["X-Fixture"] == "value"
    assert result.text == "Hello."


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "SAFETY", "RECITATION"])
def test_gemini_probe_cannot_pass_partial_or_blocked_text(finish):
    post = AsyncMock(return_value={"candidates": [{"finishReason": finish,
        "content": {"parts": [{"text": "Partial answer"}]}}]})
    with patch.object(endpoint_adapters, "post_json_request", post), pytest.raises(EndpointAdapterError):
        asyncio.run(providers.probe_provider(normalized(endpoint_type="gemini"), timeout=4))


@pytest.mark.parametrize("endpoint,payload", [
    ("openai-chat", {"choices": [{"finish_reason": "length", "message": {"content": "Partial"}}]}),
    ("openai-chat", {"choices": [{"finish_reason": "content_filter", "message": {"content": "Blocked"}}]}),
    ("openai-chat", {"choices": [{"finish_reason": "stop", "message": {"content": "Hello", "refusal": "Blocked"}}]}),
    ("openai-chat", {"choices": [{"finish_reason": "stop", "message": {"content": "Checking", "tool_calls": [{}]}}]}),
    ("openai-responses", {"status": "completed", "output_text": "Checking", "output": [{"type": "function_call", "status": "completed"}]}),
    ("openai-responses", {"status": "completed", "output_text": "Hello", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "Blocked"}]}]}),
    ("gemini", {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Checking"}, {"functionCall": {"name": "test"}}]}}]}),
    ("anthropic-messages", {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Checking"}, {"type": "tool_use"}]}),
    ("anthropic-messages", {"stop_reason": "refusal", "content": [{"type": "text", "text": "Declined"}]}),
    ("anthropic-messages", {"stop_reason": "model_context_window_exceeded", "content": [{"type": "text", "text": "Partial"}]}),
])
def test_native_probe_rejects_partial_refused_and_pending_tool_replies(endpoint, payload):
    post = AsyncMock(return_value=payload)
    cfg = normalized(endpoint_type=endpoint, provider_protocol="newapi")
    with patch.object(endpoint_adapters, "post_json_request", post), pytest.raises(EndpointAdapterError):
        asyncio.run(providers.probe_provider(cfg, timeout=4))
    post.assert_awaited_once()


def test_responses_probe_accepts_completed_provider_managed_search_with_text():
    post = AsyncMock(return_value={"status": "completed", "output_text": "Hello.",
        "output": [{"type": "web_search_call", "status": "completed"}]})
    with patch.object(endpoint_adapters, "post_json_request", post):
        result = asyncio.run(providers.probe_provider(normalized(endpoint_type="openai-responses"), timeout=4))
    assert result.text == "Hello."


class ChatClient:
    def __init__(self, response):
        self.call = AsyncMock(return_value=response)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.call))
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


def chat_response(text="Hello.", finish="stop", refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
        message=SimpleNamespace(content=text, refusal=refusal))])


def test_chat_probe_uses_runtime_body_headers_and_closes_client_without_fallback():
    cfg = normalized(url="https://openrouter.ai/api/v1", reasoning_effort="low",
        include_body="top_p: 0.75", exclude_body="- max_tokens", include_headers="X-Fixture: value",
        openrouter={"provider": {"order": ["example"]}})
    client = ChatClient(chat_response())
    with patch.object(providers, "AsyncOpenAI", return_value=client) as create:
        result = asyncio.run(providers.probe_provider(cfg, timeout=4))
    client.call.assert_awaited_once()
    request = client.call.await_args.kwargs
    assert request["model"] == cfg["model"]
    assert request["temperature"] == 0
    assert request["top_p"] == 0.75
    assert "max_tokens" not in request
    assert request["extra_body"]["reasoning_effort"] == "low"
    assert request["extra_body"]["provider"] == {"order": ["example"]}
    assert request["extra_headers"]["X-Fixture"] == "value"
    assert create.call_args.kwargs["max_retries"] == 0
    assert create.call_args.kwargs["default_headers"]["X-OpenRouter-Title"] == "Fixture"
    assert client.closed and result.text == "Hello."


@pytest.mark.parametrize("response", [chat_response(None), chat_response("  "),
    chat_response("<think>Only reasoning.</think>"), chat_response({"text": "wrong shape"}),
    chat_response("partial", "length"), chat_response("Checking", "tool_calls"),
    chat_response("blocked", refusal="refusal"), SimpleNamespace(choices=[])])
def test_chat_probe_rejects_unusable_or_incomplete_replies(response):
    client = ChatClient(response)
    with patch.object(providers, "AsyncOpenAI", return_value=client), pytest.raises(EndpointAdapterError):
        asyncio.run(providers.probe_provider(normalized(), timeout=4))
    client.call.assert_awaited_once()
    assert client.closed


@pytest.mark.parametrize("endpoint", ["openai-chat", "openai-responses"])
def test_model_override_cannot_make_a_different_model_pass(endpoint):
    cfg = normalized(endpoint_type=endpoint, include_body="model: other-model")
    post = AsyncMock()
    with patch.object(endpoint_adapters, "post_json_request", post), patch.object(providers, "AsyncOpenAI") as client:
        with pytest.raises(EndpointAdapterError):
            asyncio.run(providers.probe_provider(cfg, timeout=4))
    post.assert_not_called()
    client.assert_not_called()

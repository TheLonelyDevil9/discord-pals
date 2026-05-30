"""Dedicated NewAPI runtime adapters.

The legacy OpenAI-compatible providers keep using the OpenAI SDK. These
adapters are opt-in via ``provider_protocol: newapi`` so endpoint-specific
auth, URL, request, and response shapes cannot leak into the legacy lane.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import quote

import aiohttp

from provider_contracts import (
    EndpointType,
    GenerationResult,
    ProviderDescriptor,
    ProviderError,
    ProviderRequest,
    UNSET,
    provider_error_policy,
    reject_image_generation_disabled,
    select_newapi_auth_headers_for_endpoint,
)

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    yaml = None
    YAML_AVAILABLE = False


PostJSON = Callable[[str, Mapping[str, str], Mapping[str, Any], int], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class NewAPIHTTPStatusError(Exception):
    status: int
    body: str = ""

    def __str__(self) -> str:
        return f"NewAPI HTTP {self.status}"


class NewAPIAdapterError(Exception):
    """Adapter failure with typed, redacted provider diagnostics."""

    def __init__(self, provider_error: ProviderError):
        self.provider_error = provider_error
        super().__init__(provider_error.code)


class NewAPIProviderAdapter:
    """Serialize requests for explicit NewAPI endpoint types."""

    def __init__(self, post_json: PostJSON | None = None):
        self._post_json = post_json or post_json_request

    async def generate(
        self,
        *,
        descriptor: ProviderDescriptor,
        request: ProviderRequest,
        api_key: str,
        timeout: int,
        requires_key: bool = True,
        include_body: str = "",
        exclude_body: str = "",
        include_headers: str = "",
    ) -> GenerationResult:
        image_error = reject_image_generation_disabled(descriptor, request)
        if image_error:
            raise NewAPIAdapterError(image_error)

        endpoint = EndpointType.parse(request.endpoint_type)
        if not descriptor.capabilities.supports(endpoint):
            raise NewAPIAdapterError(
                ProviderError(
                    code="capability_unsupported",
                    message="Provider does not support the requested endpoint.",
                    provider_name=descriptor.name,
                    tier=descriptor.tier,
                    endpoint_type=endpoint,
                    retryable=False,
                    diagnostics={"endpoint_type": endpoint.value},
                )
            )

        url, body = self._build_url_and_body(descriptor, request)
        _apply_body_overrides(
            body,
            request.extra_body,
            include_body=include_body,
            exclude_body=exclude_body,
        )
        auth = select_newapi_auth_headers_for_endpoint(
            api_key,
            endpoint,
            requires_key=requires_key,
        )
        headers = {"Content-Type": "application/json", **auth.headers}
        headers.update(_parse_include_headers(include_headers))

        try:
            payload = await self._post_json(url, headers, body, timeout)
        except asyncio.TimeoutError:
            raise
        except NewAPIHTTPStatusError as error:
            raise NewAPIAdapterError(_provider_error_from_status(error.status, descriptor, endpoint, error.body))

        return self._parse_response(descriptor, request, payload)

    def _build_url_and_body(
        self,
        descriptor: ProviderDescriptor,
        request: ProviderRequest,
    ) -> tuple[str, dict[str, Any]]:
        endpoint = EndpointType.parse(request.endpoint_type)
        base_url = descriptor.newapi_base_url
        if endpoint is EndpointType.CHAT_COMPLETIONS:
            return _join_url(base_url, "chat/completions"), _openai_chat_body(request)
        if endpoint is EndpointType.RESPONSES:
            return _join_url(base_url, "responses"), _openai_responses_body(request)
        if endpoint in (EndpointType.MESSAGES, EndpointType.ANTHROPIC_MESSAGES):
            path = "messages" if base_url.rstrip("/").lower().endswith("/v1") else "v1/messages"
            return _join_url(base_url, path), _anthropic_messages_body(request)
        if endpoint is EndpointType.GEMINI:
            model = quote(request.model or descriptor.model, safe="")
            return _join_url(base_url, f"models/{model}:generateContent"), _gemini_body(request)
        raise NewAPIAdapterError(
            ProviderError(
                code="capability_unsupported",
                message="NewAPI adapter does not support this endpoint.",
                provider_name=descriptor.name,
                tier=descriptor.tier,
                endpoint_type=endpoint,
                retryable=False,
                diagnostics={"endpoint_type": endpoint.value},
            )
        )

    def _parse_response(
        self,
        descriptor: ProviderDescriptor,
        request: ProviderRequest,
        payload: Mapping[str, Any],
    ) -> GenerationResult:
        endpoint = EndpointType.parse(request.endpoint_type)
        if endpoint is EndpointType.CHAT_COMPLETIONS:
            text, reasoning = _parse_openai_chat_response(payload)
        elif endpoint is EndpointType.RESPONSES:
            text, reasoning = _parse_openai_responses_response(payload)
        elif endpoint in (EndpointType.MESSAGES, EndpointType.ANTHROPIC_MESSAGES):
            text, reasoning = _parse_anthropic_messages_response(payload)
        elif endpoint is EndpointType.GEMINI:
            text, reasoning = _parse_gemini_response(payload)
        else:
            text, reasoning = "", None

        return GenerationResult(
            text=text,
            reasoning_text=reasoning,
            provider_name=descriptor.name,
            tier=descriptor.tier,
            model=request.model or descriptor.model,
            usage=dict(payload.get("usage") or payload.get("usageMetadata") or {}),
            raw=payload,
        )


async def post_json_request(
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    timeout: int,
) -> Mapping[str, Any]:
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        async with session.post(url, headers=dict(headers), json=dict(body)) as response:
            text = await response.text()
            if response.status >= 400:
                raise NewAPIHTTPStatusError(response.status, text[:1000])
            if not text.strip():
                return {}
            return await response.json(content_type=None)


def is_newapi_provider_config(config: Mapping[str, Any]) -> bool:
    protocol = str(config.get("provider_protocol") or config.get("protocol") or "").strip().lower()
    return protocol.replace("_", "-") in {"newapi", "new-api"}


def _openai_chat_body(request: ProviderRequest) -> dict[str, Any]:
    body = {
        "model": request.model,
        "messages": _messages(request.messages),
    }
    _add_sampling(body, request, max_token_key="max_tokens")
    return body


def _openai_responses_body(request: ProviderRequest) -> dict[str, Any]:
    body = {
        "model": request.model,
        "input": _messages(request.messages),
    }
    _add_sampling(body, request, max_token_key="max_output_tokens")
    return body


def _anthropic_messages_body(request: ProviderRequest) -> dict[str, Any]:
    system_parts = []
    messages = []
    for message in _messages(request.messages):
        role = message.get("role")
        if role == "system":
            system_parts.append(_content_as_text(message.get("content")))
            continue
        messages.append({
            "role": "assistant" if role == "assistant" else "user",
            "content": _anthropic_content(message.get("content")),
        })

    body = {
        "model": request.model,
        "messages": messages,
    }
    if system_parts:
        body["system"] = "\n\n".join(part for part in system_parts if part)
    _add_sampling(body, request, max_token_key="max_tokens")
    return body


def _gemini_body(request: ProviderRequest) -> dict[str, Any]:
    system_parts = []
    contents = []
    for message in _messages(request.messages):
        role = message.get("role")
        if role == "system":
            system_parts.append(_content_as_text(message.get("content")))
            continue
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": _gemini_parts(message.get("content")),
        })

    body: dict[str, Any] = {"contents": contents}
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

    generation_config = {}
    if request.temperature is not UNSET and request.temperature is not None:
        generation_config["temperature"] = request.temperature
    if request.max_tokens is not UNSET and request.max_tokens is not None:
        generation_config["maxOutputTokens"] = request.max_tokens
    if generation_config:
        body["generationConfig"] = generation_config
    return body


def _add_sampling(body: dict[str, Any], request: ProviderRequest, *, max_token_key: str) -> None:
    if request.temperature is not UNSET and request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_tokens is not UNSET and request.max_tokens is not None:
        body[max_token_key] = request.max_tokens


def _messages(value: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if value is UNSET or value is None:
        return []
    return [dict(message) for message in value]


def _anthropic_content(content: Any) -> str | list[dict[str, Any]]:
    if not isinstance(content, list):
        return str(content or "")

    parts = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") == "text":
            parts.append({"type": "text", "text": str(part.get("text") or "")})
        elif part.get("type") == "image_url":
            image_block = _anthropic_image_block(part.get("image_url"))
            if image_block:
                parts.append(image_block)
    return parts or ""


def _anthropic_image_block(image_url: Any) -> dict[str, Any] | None:
    url = image_url.get("url") if isinstance(image_url, Mapping) else image_url
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:") and ";base64," in url:
        media_type = url.split(";", 1)[0].replace("data:", "") or "image/png"
        data = url.split(";base64,", 1)[1]
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            },
        }
    return {
        "type": "image",
        "source": {
            "type": "url",
            "url": url,
        },
    }


def _gemini_parts(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return [{"text": str(content or "")}]

    parts = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") == "text":
            parts.append({"text": str(part.get("text") or "")})
        elif part.get("type") == "image_url":
            image_part = _gemini_image_part(part.get("image_url"))
            if image_part:
                parts.append(image_part)
    return parts or [{"text": ""}]


def _gemini_image_part(image_url: Any) -> dict[str, Any] | None:
    url = image_url.get("url") if isinstance(image_url, Mapping) else image_url
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:") and ";base64," in url:
        media_type = url.split(";", 1)[0].replace("data:", "") or "image/png"
        data = url.split(";base64,", 1)[1]
        return {
            "inlineData": {
                "mimeType": media_type,
                "data": data,
            }
        }
    return {"fileData": {"fileUri": url}}


def _content_as_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, Mapping) and part.get("type") == "text"
        ).strip()
    return str(content or "")


def _apply_body_overrides(
    body: dict[str, Any],
    extra_body: Mapping[str, Any] | None,
    *,
    include_body: str = "",
    exclude_body: str = "",
) -> None:
    if include_body:
        _merge_yaml_to_dict(body, include_body)
    if exclude_body:
        _exclude_keys_by_yaml(body, exclude_body)
    if isinstance(extra_body, Mapping) and extra_body:
        _deep_merge(body, copy.deepcopy(dict(extra_body)))


def _merge_yaml_to_dict(target: dict[str, Any], yaml_string: str) -> None:
    if not yaml_string or not yaml_string.strip() or not YAML_AVAILABLE:
        return
    try:
        parsed = yaml.safe_load(yaml_string)
    except Exception:
        return
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, Mapping):
                _deep_merge(target, dict(item))
    elif isinstance(parsed, Mapping):
        _deep_merge(target, dict(parsed))


def _exclude_keys_by_yaml(target: dict[str, Any], yaml_string: str) -> None:
    if not yaml_string or not yaml_string.strip() or not YAML_AVAILABLE:
        return
    try:
        parsed = yaml.safe_load(yaml_string)
    except Exception:
        return
    if isinstance(parsed, list):
        keys = parsed
    elif isinstance(parsed, Mapping):
        keys = parsed.keys()
    else:
        keys = [parsed]
    for key in keys:
        if isinstance(key, str):
            target.pop(key, None)


def _parse_include_headers(include_headers: str) -> dict[str, str]:
    if not include_headers or not include_headers.strip() or not YAML_AVAILABLE:
        return {}
    try:
        parsed = yaml.safe_load(include_headers)
    except Exception:
        return {}
    if isinstance(parsed, Mapping):
        return {str(k): str(v) for k, v in parsed.items()}
    if isinstance(parsed, list):
        headers = {}
        for item in parsed:
            if isinstance(item, Mapping):
                headers.update({str(k): str(v) for k, v in item.items()})
        return headers
    return {}


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(base.get(key), dict) and isinstance(value, Mapping):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _parse_openai_chat_response(payload: Mapping[str, Any]) -> tuple[str, str | None]:
    choices = payload.get("choices") or []
    if not choices:
        return "", None
    message = (choices[0] or {}).get("message") or {}
    return _response_content_text(message.get("content")), _reasoning_text(message)


def _parse_openai_responses_response(payload: Mapping[str, Any]) -> tuple[str, str | None]:
    if payload.get("output_text"):
        return str(payload.get("output_text") or ""), _reasoning_text(payload)

    text_parts = []
    reasoning_parts = []
    for item in payload.get("output") or []:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for content in item.get("content") or []:
                if isinstance(content, Mapping) and content.get("type") in ("output_text", "text"):
                    text_parts.append(str(content.get("text") or ""))
        elif item_type in ("reasoning", "thinking"):
            reasoning_parts.append(_response_content_text(item.get("summary") or item.get("content")))
    return "".join(text_parts), "\n".join(part for part in reasoning_parts if part) or None


def _parse_anthropic_messages_response(payload: Mapping[str, Any]) -> tuple[str, str | None]:
    text_parts = []
    reasoning_parts = []
    for part in payload.get("content") or []:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") == "text":
            text_parts.append(str(part.get("text") or ""))
        elif part.get("type") in ("thinking", "reasoning"):
            reasoning_parts.append(str(part.get("thinking") or part.get("text") or ""))
    return "".join(text_parts), "\n".join(part for part in reasoning_parts if part) or None


def _parse_gemini_response(payload: Mapping[str, Any]) -> tuple[str, str | None]:
    candidates = payload.get("candidates") or []
    if not candidates:
        return "", None
    content = (candidates[0] or {}).get("content") or {}
    text_parts = [
        str(part.get("text") or "")
        for part in content.get("parts") or []
        if isinstance(part, Mapping) and part.get("text") is not None
    ]
    return "".join(text_parts), None


def _response_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, Mapping)
        )
    if isinstance(content, Mapping):
        if content.get("text") is not None:
            return str(content.get("text") or "")
        if content.get("summary") is not None:
            return _response_content_text(content.get("summary"))
    return ""


def _reasoning_text(value: Mapping[str, Any]) -> str | None:
    for key in ("reasoning_content", "reasoning", "thinking"):
        reasoning = value.get(key)
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning
        if isinstance(reasoning, Mapping):
            text = _response_content_text(reasoning.get("summary") or reasoning.get("content") or reasoning.get("text"))
            if text:
                return text
    return None


def _provider_error_from_status(
    status: int,
    descriptor: ProviderDescriptor,
    endpoint: EndpointType,
    body: str = "",
) -> ProviderError:
    lowered = (body or "").lower()
    is_content_filter = any(token in lowered for token in ("content filter", "safety", "blocked"))
    if is_content_filter:
        code = "content_filter"
    elif status in (401, 403):
        code = "auth"
    elif status == 429:
        code = "rate_limit"
    elif status == 408:
        code = "timeout"
    elif 500 <= status <= 599:
        code = "server_5xx"
    elif status == 400:
        code = "bad_request"
    else:
        code = "unknown"
    return ProviderError(
        code=code,
        message=f"NewAPI request failed with HTTP {status}.",
        provider_name=descriptor.name,
        tier=descriptor.tier,
        endpoint_type=endpoint,
        retryable=provider_error_policy(code).retry_current_provider,
        diagnostics={
            "status": status,
            "status_class": f"{status // 100}xx" if status else "",
            "endpoint_type": endpoint.value,
        },
    )


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

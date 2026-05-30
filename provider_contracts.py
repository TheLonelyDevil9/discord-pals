"""Pure provider gateway contracts and NewAPI helper policies.

This module is intentionally not wired into runtime provider calls yet. It gives
future provider-gateway work a typed boundary without changing legacy behavior.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class _UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _UnsetType()


class ProviderProtocol(Enum):
    """Provider wire protocol families understood by the gateway contract."""

    LEGACY_OPENAI_COMPATIBLE = "legacy-openai-compatible"
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENAI_LIKE = "openai_compatible"
    NEWAPI = "newapi"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"

    @classmethod
    def parse(cls, value: Any, default: "ProviderProtocol" | None = None) -> "ProviderProtocol":
        if value is UNSET or value is None or str(value).strip() == "":
            return default or cls.OPENAI_COMPATIBLE
        if isinstance(value, cls):
            return value

        normalized = _normalize_token(value)
        aliases = {
            "legacy_openai_compatible": cls.LEGACY_OPENAI_COMPATIBLE,
            "legacy_openai": cls.LEGACY_OPENAI_COMPATIBLE,
            "legacy": cls.LEGACY_OPENAI_COMPATIBLE,
            "openai": cls.OPENAI,
            "openai_compatible": cls.OPENAI_COMPATIBLE,
            "openai_compat": cls.OPENAI_COMPATIBLE,
            "openai_like": cls.OPENAI_COMPATIBLE,
            "oai": cls.OPENAI_COMPATIBLE,
            "oai_compatible": cls.OPENAI_COMPATIBLE,
            "chat_completions": cls.OPENAI_COMPATIBLE,
            "newapi": cls.NEWAPI,
            "new_api": cls.NEWAPI,
            "gemini": cls.GEMINI,
            "google": cls.GEMINI,
            "google_gemini": cls.GEMINI,
            "anthropic": cls.ANTHROPIC,
            "claude": cls.ANTHROPIC,
        }
        if normalized in aliases:
            return aliases[normalized]
        raise ValueError(f"Unknown provider protocol: {value!r}")

    @property
    def is_openai_like(self) -> bool:
        return self in {
            ProviderProtocol.LEGACY_OPENAI_COMPATIBLE,
            ProviderProtocol.OPENAI,
            ProviderProtocol.OPENAI_COMPATIBLE,
            ProviderProtocol.NEWAPI,
        }


class ProviderErrorCode(str, Enum):
    """Canonical provider failure categories used by fallback policy."""

    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    SERVER_5XX = "server_5xx"
    BAD_REQUEST = "bad_request"
    CONTENT_FILTER = "content_filter"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    CANCELLED = "cancelled"
    EMPTY_RESPONSE = "empty_response"
    NO_CHOICES = "no_choices"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderFallbackPolicy:
    """Observable fallback behavior for one provider error category."""

    retry_current_provider: bool
    fallback_eligible: bool
    public_notice: bool = False


PROVIDER_FALLBACK_POLICIES: Mapping[str, ProviderFallbackPolicy] = {
    ProviderErrorCode.AUTH.value: ProviderFallbackPolicy(False, True),
    ProviderErrorCode.RATE_LIMIT.value: ProviderFallbackPolicy(True, True),
    ProviderErrorCode.TIMEOUT.value: ProviderFallbackPolicy(False, True),
    ProviderErrorCode.NETWORK.value: ProviderFallbackPolicy(False, True),
    ProviderErrorCode.SERVER_5XX.value: ProviderFallbackPolicy(False, True),
    ProviderErrorCode.BAD_REQUEST.value: ProviderFallbackPolicy(False, True),
    ProviderErrorCode.CONTENT_FILTER.value: ProviderFallbackPolicy(False, True),
    ProviderErrorCode.CAPABILITY_UNSUPPORTED.value: ProviderFallbackPolicy(False, True),
    ProviderErrorCode.CANCELLED.value: ProviderFallbackPolicy(False, False),
    ProviderErrorCode.EMPTY_RESPONSE.value: ProviderFallbackPolicy(False, True),
    ProviderErrorCode.NO_CHOICES.value: ProviderFallbackPolicy(False, True),
    ProviderErrorCode.UNKNOWN.value: ProviderFallbackPolicy(False, True),
}


def provider_error_policy(code: str | ProviderErrorCode) -> ProviderFallbackPolicy:
    key = code.value if isinstance(code, ProviderErrorCode) else str(code or ProviderErrorCode.UNKNOWN.value)
    return PROVIDER_FALLBACK_POLICIES.get(key, PROVIDER_FALLBACK_POLICIES[ProviderErrorCode.UNKNOWN.value])


class EndpointType(Enum):
    """Canonical endpoint families for provider requests."""

    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    MESSAGES = "messages"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GEMINI = "gemini"
    IMAGE_GENERATIONS = "image_generations"
    IMAGE_GENERATION = "image_generations"
    EMBEDDINGS = "embeddings"

    @classmethod
    def parse(cls, value: Any, default: "EndpointType" | None = None) -> "EndpointType":
        if value is UNSET or value is None or str(value).strip() == "":
            return default or cls.CHAT_COMPLETIONS
        if isinstance(value, cls):
            return value

        normalized = _normalize_token(value)
        aliases = {
            "chat": cls.CHAT_COMPLETIONS,
            "chat_completion": cls.CHAT_COMPLETIONS,
            "chat_completions": cls.CHAT_COMPLETIONS,
            "openai_chat": cls.CHAT_COMPLETIONS,
            "openai_compatible_chat": cls.CHAT_COMPLETIONS,
            "completions": cls.CHAT_COMPLETIONS,
            "v1_chat_completions": cls.CHAT_COMPLETIONS,
            "responses": cls.RESPONSES,
            "response": cls.RESPONSES,
            "openai_responses": cls.RESPONSES,
            "openai_response": cls.RESPONSES,
            "openai_compatible": cls.CHAT_COMPLETIONS,
            "messages": cls.MESSAGES,
            "message": cls.MESSAGES,
            "anthropic_messages": cls.ANTHROPIC_MESSAGES,
            "anthropic": cls.ANTHROPIC_MESSAGES,
            "claude": cls.ANTHROPIC_MESSAGES,
            "gemini": cls.GEMINI,
            "google": cls.GEMINI,
            "google_gemini": cls.GEMINI,
            "images": cls.IMAGE_GENERATIONS,
            "image": cls.IMAGE_GENERATIONS,
            "image_generation": cls.IMAGE_GENERATIONS,
            "image_generations": cls.IMAGE_GENERATIONS,
            "image_generation_disabled": cls.IMAGE_GENERATIONS,
            "images_generations": cls.IMAGE_GENERATIONS,
            "v1_images_generations": cls.IMAGE_GENERATIONS,
            "embeddings": cls.EMBEDDINGS,
            "embedding": cls.EMBEDDINGS,
        }
        if normalized in aliases:
            return aliases[normalized]
        raise ValueError(f"Unknown endpoint type: {value!r}")


@dataclass(frozen=True)
class CapabilityFlags:
    """Provider capabilities relevant to gateway selection."""

    chat: bool = True
    vision: bool = True
    image_generation: bool = False
    image_generation_modeled: bool = False
    reasoning: bool = False
    embeddings: bool = False
    streaming: bool = False

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "CapabilityFlags":
        config = config or {}
        image_generation_enabled = bool(
            config.get("supports_image_generation", config.get("image_generation", False))
        )
        image_generation_modeled = bool(
            config.get(
                "image_generation_modeled",
                image_generation_enabled or "supports_image_generation" in config or "image_generation" in config,
            )
        )
        if config.get("image_generation_disabled"):
            image_generation_enabled = False
            image_generation_modeled = True
        return cls(
            chat=bool(config.get("supports_chat", config.get("chat", True))),
            vision=bool(config.get("supports_vision", config.get("vision", True))),
            image_generation=image_generation_enabled,
            image_generation_modeled=image_generation_modeled,
            reasoning=bool(config.get("supports_reasoning", config.get("reasoning", False))),
            embeddings=bool(config.get("supports_embeddings", config.get("embeddings", False))),
            streaming=bool(config.get("supports_streaming", config.get("streaming", False))),
        )

    def supports(self, endpoint_type: EndpointType | str | None) -> bool:
        endpoint = EndpointType.parse(endpoint_type)
        if endpoint in (
            EndpointType.CHAT_COMPLETIONS,
            EndpointType.RESPONSES,
            EndpointType.MESSAGES,
            EndpointType.ANTHROPIC_MESSAGES,
            EndpointType.GEMINI,
        ):
            return self.chat
        if endpoint is EndpointType.IMAGE_GENERATIONS:
            return self.image_generation
        if endpoint is EndpointType.EMBEDDINGS:
            return self.embeddings
        return False


@dataclass(frozen=True)
class ProviderDescriptor:
    """Static provider metadata before a request is built."""

    name: str
    tier: str = ""
    protocol: ProviderProtocol = ProviderProtocol.OPENAI_COMPATIBLE
    endpoint_type: EndpointType = EndpointType.CHAT_COMPLETIONS
    base_url: str = ""
    model: str = ""
    capabilities: CapabilityFlags = field(default_factory=CapabilityFlags)
    append_base_path: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        tier: str = "",
        default_protocol: ProviderProtocol = ProviderProtocol.OPENAI_COMPATIBLE,
    ) -> "ProviderDescriptor":
        protocol = ProviderProtocol.parse(
            config.get("provider_protocol", config.get("protocol", UNSET)),
            default=default_protocol,
        )
        endpoint_type = EndpointType.parse(config.get("endpoint_type", config.get("endpoint", UNSET)))
        return cls(
            name=str(config.get("name") or tier or "Provider"),
            tier=tier,
            protocol=protocol,
            endpoint_type=endpoint_type,
            base_url=str(config.get("url") or config.get("base_url") or ""),
            model=str(config.get("model") or ""),
            capabilities=CapabilityFlags.from_config(config),
            append_base_path=bool(config.get("append_base_path", True)),
            metadata=dict(config.get("metadata") or {}),
        )

    @property
    def newapi_base_url(self) -> str:
        return newapi_base_url_for_endpoint(
            self.base_url,
            self.endpoint_type,
            append_base_path=self.append_base_path,
        )


@dataclass(frozen=True)
class ProviderRequest:
    """Normalized request envelope before provider-specific serialization."""

    endpoint_type: EndpointType = EndpointType.CHAT_COMPLETIONS
    model: str = ""
    messages: Sequence[Mapping[str, Any]] | _UnsetType | None = UNSET
    prompt: str | _UnsetType | None = UNSET
    input: Any = UNSET
    temperature: float | _UnsetType | None = UNSET
    max_tokens: int | _UnsetType | None = UNSET
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderError:
    """Typed provider failure that can be logged or routed without exceptions."""

    code: str
    message: str
    provider_name: str = ""
    tier: str = ""
    endpoint_type: EndpointType | None = None
    retryable: bool = False
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


def provider_error_code_from_exception(error: BaseException) -> ProviderErrorCode:
    """Classify provider exceptions without exposing raw exception text."""

    if isinstance(error, asyncio.CancelledError):
        return ProviderErrorCode.CANCELLED
    if isinstance(error, asyncio.TimeoutError):
        return ProviderErrorCode.TIMEOUT

    type_name = type(error).__name__.lower()
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    body_text = " ".join(
        str(part or "")
        for part in (
            getattr(error, "message", ""),
            getattr(error, "body", ""),
            getattr(error, "response", ""),
            error,
        )
    ).lower()

    if "ratelimit" in type_name or "rate_limit" in type_name or status == 429:
        return ProviderErrorCode.RATE_LIMIT
    if "timeout" in type_name or status == 408:
        return ProviderErrorCode.TIMEOUT
    if "authentication" in type_name or "permissiondenied" in type_name:
        return ProviderErrorCode.AUTH
    if "contentfilter" in type_name or _looks_like_content_filter(body_text):
        return ProviderErrorCode.CONTENT_FILTER
    if "badrequest" in type_name:
        return ProviderErrorCode.BAD_REQUEST
    if "connection" in type_name or "network" in type_name or isinstance(error, (ConnectionError, OSError)):
        return ProviderErrorCode.NETWORK

    try:
        status_int = int(status)
    except (TypeError, ValueError):
        status_int = 0
    if status_int in (401, 403):
        return ProviderErrorCode.AUTH
    if status_int == 400:
        return ProviderErrorCode.BAD_REQUEST
    if 500 <= status_int <= 599:
        return ProviderErrorCode.SERVER_5XX

    return ProviderErrorCode.UNKNOWN


def provider_error_from_exception(
    error: BaseException,
    *,
    provider_name: str = "",
    tier: str = "",
    endpoint_type: EndpointType | str | None = None,
) -> ProviderError:
    """Build a redacted provider error for fallback and diagnostics."""

    code = provider_error_code_from_exception(error)
    policy = provider_error_policy(code)
    endpoint = EndpointType.parse(endpoint_type) if endpoint_type else None
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    diagnostics: dict[str, Any] = {"error_type": type(error).__name__}
    try:
        status_int = int(status)
    except (TypeError, ValueError):
        status_int = 0
    if status_int:
        diagnostics["status_class"] = f"{status_int // 100}xx"

    return ProviderError(
        code=code.value,
        message=f"Provider failed with {code.value}.",
        provider_name=provider_name,
        tier=tier,
        endpoint_type=endpoint,
        retryable=policy.retry_current_provider,
        diagnostics=diagnostics,
    )


@dataclass(frozen=True)
class GenerationResult:
    """Provider output with deliverable text separated from reasoning metadata."""

    text: str
    reasoning_text: str | None = None
    provider_name: str = ""
    tier: str = ""
    model: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw: Any = None

    @property
    def deliverable_text(self) -> str:
        return self.text

    @property
    def has_reasoning(self) -> bool:
        return bool(self.reasoning_text)


@dataclass(frozen=True, repr=False)
class AuthHeaderSelection:
    """Auth headers plus diagnostics that are safe to emit to logs."""

    headers: Mapping[str, str]
    diagnostics: Mapping[str, Any]

    @property
    def redacted_headers(self) -> dict[str, str]:
        return {name: "[REDACTED]" for name in self.headers}

    def __repr__(self) -> str:
        return (
            "AuthHeaderSelection("
            f"headers={self.redacted_headers!r}, "
            f"diagnostics={dict(self.diagnostics)!r})"
        )


def newapi_base_url(
    base_url: str,
    protocol: ProviderProtocol | str | None = ProviderProtocol.OPENAI_COMPATIBLE,
    *,
    append_base_path: bool = True,
) -> str:
    """Return the NewAPI base URL for a protocol without appending twice."""

    cleaned = str(base_url or "").strip().rstrip("/")
    if not cleaned:
        raise ValueError("base_url is required")
    if not append_base_path:
        return cleaned

    resolved_protocol = ProviderProtocol.parse(protocol)
    suffix_by_protocol = {
        ProviderProtocol.OPENAI: "/v1",
        ProviderProtocol.OPENAI_COMPATIBLE: "/v1",
        ProviderProtocol.NEWAPI: "/v1",
        ProviderProtocol.GEMINI: "/v1beta",
        ProviderProtocol.ANTHROPIC: "",
    }
    suffix = suffix_by_protocol[resolved_protocol]
    if not suffix:
        return cleaned
    if cleaned.lower().endswith(suffix.lower()):
        return cleaned
    return f"{cleaned}{suffix}"


def newapi_base_url_for_endpoint(
    base_url: str,
    endpoint_type: EndpointType | str | None = EndpointType.CHAT_COMPLETIONS,
    *,
    append_base_path: bool = True,
) -> str:
    """Return the NewAPI base URL selected by explicit endpoint type."""

    cleaned = str(base_url or "").strip().rstrip("/")
    if not cleaned:
        raise ValueError("base_url is required")
    if not append_base_path:
        return cleaned

    endpoint = EndpointType.parse(endpoint_type)
    suffix_by_endpoint = {
        EndpointType.CHAT_COMPLETIONS: "/v1",
        EndpointType.RESPONSES: "/v1",
        EndpointType.MESSAGES: "/v1",
        EndpointType.ANTHROPIC_MESSAGES: "",
        EndpointType.GEMINI: "/v1beta",
        EndpointType.IMAGE_GENERATIONS: "/v1",
        EndpointType.EMBEDDINGS: "/v1",
    }
    suffix = suffix_by_endpoint[endpoint]
    if not suffix:
        return cleaned
    if cleaned.lower().endswith(suffix.lower()):
        return cleaned
    return f"{cleaned}{suffix}"


def select_newapi_auth_headers(
    api_key: str | _UnsetType | None,
    protocol: ProviderProtocol | str | None = ProviderProtocol.OPENAI_COMPATIBLE,
    *,
    requires_key: bool = True,
) -> AuthHeaderSelection:
    """Select provider-native auth headers and redacted diagnostic fields."""

    resolved_protocol = ProviderProtocol.parse(protocol)
    key = "" if api_key is UNSET or api_key is None else str(api_key)
    if not requires_key and key.strip().lower() == "not-needed":
        key = ""
    if not key and not requires_key:
        return AuthHeaderSelection(
            headers={},
            diagnostics={
                "auth_scheme": "none",
                "header_name": "",
                "has_api_key": False,
                "requires_key": False,
            },
        )

    if resolved_protocol is ProviderProtocol.GEMINI:
        header_name = "x-goog-api-key"
        header_value = key
        scheme = "google_api_key"
    elif resolved_protocol is ProviderProtocol.ANTHROPIC:
        header_name = "x-api-key"
        header_value = key
        scheme = "anthropic_api_key"
    else:
        header_name = "Authorization"
        header_value = f"Bearer {key}" if key else ""
        scheme = "bearer"

    headers = {header_name: header_value} if key else {}
    return AuthHeaderSelection(
        headers=headers,
        diagnostics={
            "auth_scheme": scheme if key else "missing",
            "header_name": header_name,
            "has_api_key": bool(key),
            "requires_key": requires_key,
            "header_value": "[REDACTED]" if key else "",
        },
    )


def select_newapi_auth_headers_for_endpoint(
    api_key: str | _UnsetType | None,
    endpoint_type: EndpointType | str | None = EndpointType.CHAT_COMPLETIONS,
    *,
    requires_key: bool = True,
) -> AuthHeaderSelection:
    """Select NewAPI auth headers using explicit endpoint family defaults."""

    endpoint = EndpointType.parse(endpoint_type)
    protocol_by_endpoint = {
        EndpointType.GEMINI: ProviderProtocol.GEMINI,
        EndpointType.ANTHROPIC_MESSAGES: ProviderProtocol.ANTHROPIC,
        EndpointType.MESSAGES: ProviderProtocol.ANTHROPIC,
    }
    protocol = protocol_by_endpoint.get(endpoint, ProviderProtocol.OPENAI_COMPATIBLE)
    return select_newapi_auth_headers(api_key, protocol, requires_key=requires_key)


def reject_image_generation_disabled(
    descriptor: ProviderDescriptor,
    request: ProviderRequest,
) -> ProviderError | None:
    """Reject image generation when the descriptor lacks the capability."""

    endpoint = EndpointType.parse(request.endpoint_type)
    if endpoint is not EndpointType.IMAGE_GENERATIONS:
        return None
    if descriptor.capabilities.image_generation:
        return None
    return ProviderError(
        code="capability_unsupported",
        message="Provider does not support image generation.",
        provider_name=descriptor.name,
        tier=descriptor.tier,
        endpoint_type=endpoint,
        retryable=False,
        diagnostics={
            "capability": "image_generation",
            "provider": descriptor.name,
            "tier": descriptor.tier,
        },
    )


def canonical_provider_body(value: Any) -> Any:
    """Normalize request bodies for structural golden-master comparisons."""

    if value is UNSET:
        return "<UNSET>"
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {
            str(key): canonical_provider_body(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [canonical_provider_body(item) for item in value]
    return value


def provider_bodies_equal(left: Any, right: Any) -> bool:
    """Compare provider bodies after canonical structural normalization."""

    return canonical_provider_body(left) == canonical_provider_body(right)


def parse_endpoint_type(value: Any, default: EndpointType | None = None) -> EndpointType:
    return EndpointType.parse(value, default=default)


def parse_provider_protocol(value: Any, default: ProviderProtocol | None = None) -> ProviderProtocol:
    return ProviderProtocol.parse(value, default=default)


def _looks_like_content_filter(text: str) -> bool:
    return any(token in text for token in ("content filter", "content_filter", "safety", "blocked"))


def _normalize_token(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_").strip("_")


__all__ = [
    "AuthHeaderSelection",
    "CapabilityFlags",
    "EndpointType",
    "GenerationResult",
    "PROVIDER_FALLBACK_POLICIES",
    "ProviderDescriptor",
    "ProviderError",
    "ProviderErrorCode",
    "ProviderFallbackPolicy",
    "ProviderProtocol",
    "ProviderRequest",
    "UNSET",
    "canonical_provider_body",
    "newapi_base_url",
    "newapi_base_url_for_endpoint",
    "parse_endpoint_type",
    "parse_provider_protocol",
    "provider_bodies_equal",
    "provider_error_code_from_exception",
    "provider_error_from_exception",
    "provider_error_policy",
    "reject_image_generation_disabled",
    "select_newapi_auth_headers",
    "select_newapi_auth_headers_for_endpoint",
]

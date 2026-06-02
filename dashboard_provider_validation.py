"""Dashboard provider payload validation helpers."""

from __future__ import annotations

import os


TEXT_PROVIDER_REQUIRED_FIELDS = ("url", "model", "auth")
IMAGE_PROVIDER_REQUIRED_FIELDS = ("url", "model", "auth")


def _dashboard_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False
    return default


def _auth_field_status(provider: dict) -> dict:
    api_key_configured = bool(str(provider.get("api_key") or "").strip())
    key_env = str(provider.get("key_env") or "").strip()
    key_env_configured = bool(key_env and os.getenv(key_env))
    return {
        "api_key": api_key_configured,
        "key_env": bool(key_env),
        "key_env_configured": key_env_configured,
        "auth": api_key_configured or key_env_configured,
        "auth_source": "api_key" if api_key_configured else ("key_env" if key_env else ""),
    }


def _provider_field_status(provider: dict, *, provider_kind: str, index: int) -> dict:
    tier = provider_tier_name(index)
    endpoint = str(provider.get("url") or provider.get("base_url") or "").strip()
    model = str(provider.get("model") or "").strip()
    requires_key = _dashboard_bool(provider.get("requires_key"), True)
    auth_status = _auth_field_status(provider)
    required_fields = list(IMAGE_PROVIDER_REQUIRED_FIELDS if provider_kind == "image" else TEXT_PROVIDER_REQUIRED_FIELDS)

    configured_fields = {
        "url": bool(endpoint),
        "model": bool(model),
        "auth": auth_status["auth"] or not requires_key,
        "api_key": auth_status["api_key"],
        "key_env": auth_status["key_env"],
        "key_env_configured": auth_status["key_env_configured"],
        "requires_key": requires_key,
    }
    missing_required = [
        field for field in required_fields
        if field != "auth" and not configured_fields.get(field)
    ]
    if requires_key and not auth_status["auth"]:
        missing_required.append("auth")

    return {
        "index": index,
        "tier": tier,
        "kind": provider_kind,
        "name": provider.get("name") or (f"Image Provider {index + 1}" if provider_kind == "image" else f"Provider {index + 1}"),
        "model": model,
        "url": endpoint,
        "required_fields": required_fields,
        "configured_fields": configured_fields,
        "missing_required": missing_required,
        "configured": not missing_required,
        "auth_source": auth_status["auth_source"],
    }


def summarize_provider_configs(provider_list: list) -> list[dict]:
    return [
        _provider_field_status(provider, provider_kind="text", index=index)
        for index, provider in enumerate(provider_list if isinstance(provider_list, list) else [])
        if isinstance(provider, dict)
    ]


def summarize_image_provider_configs(provider_list: list) -> list[dict]:
    return [
        _provider_field_status(provider, provider_kind="image", index=index)
        for index, provider in enumerate(provider_list if isinstance(provider_list, list) else [])
        if isinstance(provider, dict)
    ]


def provider_config_schema() -> dict:
    return {
        "text_provider": {
            "required_fields": list(TEXT_PROVIDER_REQUIRED_FIELDS),
            "auth": "Set api_key directly or set key_env to an environment variable containing the key. Set requires_key=false for local/keyless providers.",
        },
        "image_provider": {
            "required_fields": list(IMAGE_PROVIDER_REQUIRED_FIELDS),
            "auth": "Set api_key directly or set key_env to an environment variable containing the key. Set requires_key=false for local/keyless providers.",
        },
    }


def validate_providers_json_payload(data: dict) -> str | None:
    """Return a validation error for malformed provider config, else None."""

    providers = data.get("providers", [])
    if providers is None:
        providers = []
    if not isinstance(providers, list):
        return "providers must be a list"

    object_fields = ("extra_body", "reasoning", "output_config", "thinking", "openrouter")
    string_fields = (
        "name", "url", "base_url", "api_key", "key_env", "provider_protocol",
        "protocol", "endpoint_type", "endpoint", "model", "reasoning_effort",
        "effort", "reasoning_format", "include_body", "exclude_body",
        "include_headers",
    )
    bool_fields = (
        "requires_key", "append_base_path", "supports_chat", "supports_vision",
        "supports_reasoning", "supports_streaming", "image_generation_modeled",
        "image_generation_disabled",
    )

    for index, provider in enumerate(providers, start=1):
        if not isinstance(provider, dict):
            return f"Provider {index} must be an object"
        endpoint = provider.get("url") or provider.get("base_url")
        if not isinstance(endpoint, str) or not endpoint.strip():
            return f"Provider {index} needs an API endpoint"
        model = provider.get("model")
        if "model" in provider and (not isinstance(model, str) or not model.strip()):
            return f"Provider {index} needs a model"
        for key in object_fields:
            if key in provider and provider[key] is not None and not isinstance(provider[key], dict):
                return f"Provider {index} {key} must be an object"
        for key in string_fields:
            if key in provider and provider[key] is not None and not isinstance(provider[key], str):
                return f"Provider {index} {key} must be a string"
        for key in bool_fields:
            if key not in provider or provider[key] is None or isinstance(provider[key], bool):
                continue
            if not (
                isinstance(provider[key], str)
                and provider[key].strip().lower() in ("true", "false", "1", "0", "yes", "no", "on", "off")
            ):
                return f"Provider {index} {key} must be true or false"
        for key in ("max_tokens", "timeout"):
            if key in provider and provider[key] not in (None, ""):
                try:
                    int(provider[key])
                except (TypeError, ValueError):
                    return f"Provider {index} {key} must be a number"
        if "temperature" in provider and provider["temperature"] not in (None, ""):
            try:
                float(provider["temperature"])
            except (TypeError, ValueError):
                return f"Provider {index} temperature must be a number"

    if "character_providers" in data and not isinstance(data["character_providers"], dict):
        return "character_providers must be an object"
    if "image_providers" in data and not isinstance(data["image_providers"], list):
        return "image_providers must be a list"
    return None


def provider_tier_name(index: int) -> str:
    names = ["primary", "secondary", "fallback"]
    return names[index] if index < len(names) else f"tier_{index}"


def summarize_image_providers(provider_list: list) -> list[dict]:
    providers = []
    for index, provider in enumerate(provider_list if isinstance(provider_list, list) else []):
        if not isinstance(provider, dict):
            continue
        providers.append({
            "index": index,
            "tier": provider_tier_name(index),
            "name": provider.get("name") or f"Image Provider {index + 1}",
            "model": provider.get("model") or "gpt-image-1",
            "url": provider.get("url") or provider.get("base_url") or "",
            "size": provider.get("size") or "1024x1024",
        })
    return providers

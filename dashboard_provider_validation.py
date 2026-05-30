"""Dashboard provider payload validation helpers."""

from __future__ import annotations


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

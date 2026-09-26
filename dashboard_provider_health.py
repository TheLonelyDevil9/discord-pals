"""Dashboard provider health validation helpers."""

from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlsplit

import logger as log


def sanitize_error_message(error: Exception) -> str:
    """Sanitize error message to avoid leaking sensitive info."""
    msg = log.redact(str(error))
    msg = re.sub(r'[A-Za-z]:\\[^\s]+', '[path]', msg)
    msg = re.sub(r'/[^\s]+/', '[path]/', msg)
    msg = re.sub(r'(api[_-]?key|token|secret|password)[=:]\s*\S+', r'\1=[redacted]', msg, flags=re.IGNORECASE)
    return msg[:200]


PROVIDER_TEST_TIMEOUT = 30


def provider_test_failure(detail: str) -> dict:
    """Make a config failure response without including provider data or secrets."""
    return {
        "success": False,
        "status": "failed",
        "provider_name": "",
        "model": "",
        "endpoint_type": "",
        "duration_ms": 0,
        "error": detail,
        "checks": [
            {"key": "configuration", "label": "Saved settings", "status": "failed", "detail": detail},
            {"key": "model_reply", "label": "Model reply", "status": "not_verified",
             "detail": "No model request was sent."},
            {"key": "other_capabilities", "label": "Images and tools", "status": "not_verified",
             "detail": "This text test does not check image input or tool calling."},
        ],
    }


def test_provider_connection(provider: dict, *, index: int = 0, default_timeout=60) -> dict:
    """Test one saved provider through the runtime's single-provider generation path."""
    from config import normalize_provider_config
    from dashboard_provider_validation import validate_providers_json_payload
    from provider_contracts import EndpointType, ProviderDescriptor
    from providers import probe_provider

    started = time.perf_counter()
    result = provider_test_failure("Saved provider settings are invalid. Review and save this provider.")
    if not isinstance(provider, dict):
        return result
    try:
        validation_error = validate_providers_json_payload({"providers": [provider]})
    except (TypeError, ValueError, OverflowError):
        return result
    if validation_error:
        return provider_test_failure(validation_error)

    try:
        if not str(provider.get("model") or "").strip():
            return provider_test_failure("Choose and save a model before testing.")
        normalized = normalize_provider_config(provider, index=index)
        descriptor = ProviderDescriptor.from_config(normalized, tier="test")
        endpoint = descriptor.endpoint_type
        result.update(
            provider_name=log.redact(descriptor.name),
            model=log.redact(descriptor.model),
            endpoint_type=endpoint.value,
        )
        address = urlsplit(normalized["url"])
        if address.scheme not in ("http", "https") or not address.netloc:
            return _configuration_failure(result, "Save an HTTP or HTTPS base URL before testing.", started)
        if endpoint not in (EndpointType.CHAT_COMPLETIONS, EndpointType.RESPONSES,
                             EndpointType.MESSAGES, EndpointType.ANTHROPIC_MESSAGES, EndpointType.GEMINI):
            return _configuration_failure(result, "Choose a supported chat API format for this provider.", started)
        if not descriptor.capabilities.supports(endpoint):
            return _configuration_failure(result, "Enable chat support for this provider before testing.", started)
        key = normalized.get("key") or ""
        if normalized.get("requires_key", True) and key.strip().lower() in ("", "not-needed"):
            return _configuration_failure(result,
                "Provider credentials are missing. Set an API key or choose No key required.", started)
        timeout = min(PROVIDER_TEST_TIMEOUT, max(1, float(normalized.get("timeout") or default_timeout)))
    except (TypeError, ValueError, OverflowError):
        # Normalizers and URL parsers may include input text in their exceptions.
        # Keep configuration errors independent of keys, headers and URLs.
        return _configuration_failure(result,
            "Check the saved URL, API format, model, credentials, and chat support settings.", started)

    result["checks"][0].update(status="passed", detail="Saved settings and required credentials are present.")

    async def run_probe():
        return await asyncio.wait_for(probe_provider(normalized, timeout=timeout), timeout=timeout)

    try:
        reply = asyncio.run(run_probe())
        if reply is None or not isinstance(reply.deliverable_text, str) or not reply.deliverable_text.strip():
            detail = "The model returned no usable text. Check the model and response settings."
            result["checks"][1].update(status="failed", detail=detail)
            result["error"] = detail
        else:
            result.update(success=True, status="passed")
            result.pop("error", None)
            result["checks"][1].update(
                status="passed",
                detail=f"The selected model returned usable text within the {timeout:g}-second test limit. "
                       "No other configured provider was tried. Regular calls may use a longer timeout.",
            )
    except Exception as error:
        detail = _probe_error_detail(error, timeout)
        result["checks"][1].update(status="failed", detail=detail)
        result["error"] = detail
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def _configuration_failure(result: dict, detail: str, started: float) -> dict:
    result["error"] = detail
    result["checks"][0]["detail"] = detail
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def _probe_error_detail(error: Exception, timeout: float) -> str:
    """Translate structured error categories; never expose provider response text."""
    from provider_contracts import provider_error_code_from_exception

    provider_error = getattr(error, "provider_error", None)
    code = getattr(provider_error, "code", None) or provider_error_code_from_exception(error).value
    diagnostics = getattr(provider_error, "diagnostics", {})
    status = diagnostics.get("status") if isinstance(diagnostics, dict) else None
    status = status or getattr(error, "status_code", None) or getattr(error, "status", None)
    if status == 404:
        return "The endpoint or model was not found. Check the API format, base URL, and exact model ID."
    details = {
        "auth": "The provider rejected the credentials. Check the API key and model access.",
        "rate_limit": "The provider returned a rate limit or quota error. Check usage and try again later.",
        "timeout": f"The model did not finish within the {timeout:g}-second test limit. "
                   "Try again or check provider availability; regular calls may use a longer timeout.",
        "network": "The provider could not be reached. Check the base URL and server network access.",
        "server_5xx": "The provider reported a server error. Check its availability and try again later.",
        "bad_request": "The provider rejected the request settings. Check the API format, model, and advanced parameters.",
        "content_filter": "The provider blocked the test response. Check its safety settings or selected model.",
        "capability_unsupported": "The selected API format is not supported by this provider's saved settings.",
        "empty_response": "The model returned no usable text. Check the model, output limit, and reasoning settings.",
        "no_choices": "The model returned no usable text. Check the API format and exact model ID.",
        "incomplete_response": "The model did not complete a final text reply. Check the output limit, reasoning, and tool settings.",
    }
    return details.get(code, "The model test failed. Check the API format, model, and provider availability.")

"""Dashboard provider health validation helpers."""

from __future__ import annotations

import os
import re

import logger as log


def sanitize_error_message(error: Exception) -> str:
    """Sanitize error message to avoid leaking sensitive info."""
    msg = log.redact(str(error))
    msg = re.sub(r'[A-Za-z]:\\[^\s]+', '[path]', msg)
    msg = re.sub(r'/[^\s]+/', '[path]/', msg)
    msg = re.sub(r'(api[_-]?key|token|secret|password)[=:]\s*\S+', r'\1=[redacted]', msg, flags=re.IGNORECASE)
    return msg[:200]


def test_newapi_provider_config(provider: dict) -> dict:
    """Validate a NewAPI provider configuration without sending prompt data."""
    from newapi_adapters import is_newapi_provider_config
    from provider_contracts import (
        CapabilityFlags,
        EndpointType,
        ProviderDescriptor,
        reject_image_generation_disabled,
        select_newapi_auth_headers_for_endpoint,
    )

    if not is_newapi_provider_config(provider):
        return {'handled': False}

    if not (provider.get('url') or provider.get('base_url')):
        return {'handled': True, 'success': False, 'error': 'Provider missing URL'}
    if not provider.get('model'):
        return {'handled': True, 'success': False, 'error': 'Provider missing model'}

    requires_key = _dashboard_bool(provider.get('requires_key'), True)
    api_key = _provider_api_key(provider)
    if requires_key and not api_key:
        return {'handled': True, 'success': False, 'error': 'Provider API key is not configured'}
    if not requires_key and api_key.strip().lower() == 'not-needed':
        api_key = ''

    try:
        descriptor = ProviderDescriptor.from_config(provider, tier='test')
        endpoint = EndpointType.parse(descriptor.endpoint_type)
        if not descriptor.capabilities.supports(endpoint):
            return {
                'handled': True,
                'success': False,
                'error': f'Endpoint {endpoint.value} is not enabled by provider capabilities',
            }
        image_error = reject_image_generation_disabled(
            descriptor,
            type('Request', (), {'endpoint_type': endpoint})(),
        )
        if image_error and endpoint is EndpointType.IMAGE_GENERATIONS:
            return {'handled': True, 'success': False, 'error': image_error.message}

        auth = select_newapi_auth_headers_for_endpoint(api_key, endpoint, requires_key=requires_key)
        return {
            'handled': True,
            'success': True,
            'message': 'NewAPI provider configuration is valid',
            'endpoint_type': endpoint.value,
            'base_url': descriptor.newapi_base_url,
            'auth_header': auth.diagnostics.get('header_name', ''),
            'capabilities': CapabilityFlags.from_config(provider).__dict__,
        }
    except Exception as e:
        return {'handled': True, 'success': False, 'error': sanitize_error_message(e)}


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


def _provider_api_key(provider: dict) -> str:
    if provider.get('api_key'):
        return str(provider['api_key'])
    if provider.get('key_env'):
        return os.getenv(str(provider['key_env']), '')
    return ''

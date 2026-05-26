"""
Discord Pals - AI Providers
3-tier fallback system using OpenAI-compatible API with rate limit handling.
"""

from openai import AsyncOpenAI, RateLimitError, APIError
import base64
from typing import List, Dict, Optional
from config import PROVIDERS, IMAGE_PROVIDERS, API_TIMEOUT
from discord_utils import remove_thinking_tags
import asyncio
import copy
import time
import logger as log

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    log.warn("PyYAML not installed - include_body/exclude_body features disabled")


MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff: 1s, 2s, 4s


REASONING_EFFORT_ALIASES = {
    "off": "none",
    "disabled": "none",
    "disable": "none",
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "extra high": "xhigh",
    "x-high": "xhigh",
    "x_high": "xhigh",
    "max-effort": "max",
    "max_effort": "max",
}

REASONING_FORMAT_ALIASES = {
    "openai": "openai_responses",
    "openai_responses": "openai_responses",
    "responses": "openai_responses",
    "reasoning": "openai_responses",
    "reasoning_object": "openai_responses",
    "reasoning-object": "openai_responses",
    "openai_chat": "openai_chat",
    "openai-chat": "openai_chat",
    "chat": "openai_chat",
    "chat_completions": "openai_chat",
    "chat-completions": "openai_chat",
    "oai_compatible": "openai_chat",
    "oai-compatible": "openai_chat",
    "reasoning_effort": "openai_chat",
    "reasoning-effort": "openai_chat",
    "claude": "claude",
    "anthropic": "claude",
    "output_config": "claude",
    "output-config": "claude",
    "effort": "effort",
    "top_level_effort": "effort",
    "top-level-effort": "effort",
    "thinking": "thinking",
}


def deep_merge_dict(base: dict, override: dict) -> None:
    """Deep merge override dict into base dict in-place."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge_dict(base[key], value)
        else:
            base[key] = value


def merge_yaml_to_dict(target: dict, yaml_string: str) -> None:
    """
    Merge YAML-formatted string into target dict (SillyTavern style).
    Supports both object notation and array of objects.
    Uses deep merge for nested objects (e.g., GLM thinking config).

    Example YAML:
        min_p: 0.1
        top_k: 40
    Or:
        - min_p: 0.1
        - top_k: 40
    Or nested:
        thinking:
          type: disabled
    """
    if not yaml_string or not yaml_string.strip():
        return
    if not YAML_AVAILABLE:
        return
    try:
        parsed = yaml.safe_load(yaml_string)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    deep_merge_dict(target, item)
        elif isinstance(parsed, dict):
            deep_merge_dict(target, parsed)
    except Exception as e:
        log.debug(f"Failed to parse include_body YAML: {e}")


def exclude_keys_by_yaml(target: dict, yaml_string: str) -> None:
    """
    Remove keys from dict based on YAML list or object keys (SillyTavern style).

    Example YAML:
        - frequency_penalty
        - presence_penalty
    Or as object:
        frequency_penalty: true
        presence_penalty: true
    """
    if not yaml_string or not yaml_string.strip():
        return
    if not YAML_AVAILABLE:
        return
    try:
        parsed = yaml.safe_load(yaml_string)
        if isinstance(parsed, list):
            for key in parsed:
                if isinstance(key, str):
                    target.pop(key, None)
        elif isinstance(parsed, dict):
            for key in parsed.keys():
                target.pop(key, None)
        elif isinstance(parsed, str):
            target.pop(parsed, None)
    except Exception as e:
        log.debug(f"Failed to parse exclude_body YAML: {e}")


def normalize_reasoning_effort(value) -> str:
    """Normalize common provider effort spellings while preserving unknown values."""
    if value is None:
        return ""

    effort = str(value).strip()
    if not effort:
        return ""

    normalized = effort.lower()
    return REASONING_EFFORT_ALIASES.get(normalized, normalized)


def resolve_reasoning_format(provider_cfg: dict) -> str:
    """Resolve the configured reasoning payload shape for one provider."""
    configured = str(provider_cfg.get("reasoning_format") or "auto").strip().lower()
    configured = configured.replace(" ", "_")
    if configured and configured != "auto":
        return REASONING_FORMAT_ALIASES.get(configured, configured)

    model_and_url = " ".join([
        str(provider_cfg.get("model", "")),
        str(provider_cfg.get("url", "")),
        str(provider_cfg.get("name", "")),
    ]).lower()
    if "claude" in model_and_url or "anthropic" in model_and_url:
        return "claude"
    return "openai_chat"


def build_reasoning_extra_body(provider_cfg: dict) -> dict:
    """Build provider-specific reasoning controls for the Chat Completions body."""
    extra_body = {}

    for key in ("reasoning", "output_config", "thinking"):
        value = provider_cfg.get(key)
        if isinstance(value, dict) and value:
            extra_body[key] = copy.deepcopy(value)

    effort = normalize_reasoning_effort(
        provider_cfg.get("reasoning_effort") or provider_cfg.get("effort")
    )
    if effort:
        reasoning_format = resolve_reasoning_format(provider_cfg)
        if reasoning_format == "openai_responses":
            reasoning = extra_body.get("reasoning")
            if not isinstance(reasoning, dict):
                reasoning = {}
            reasoning["effort"] = effort
            extra_body["reasoning"] = reasoning
        elif reasoning_format == "claude":
            output_config = extra_body.get("output_config")
            if not isinstance(output_config, dict):
                output_config = {}
            output_config["effort"] = effort
            extra_body["output_config"] = output_config
        elif reasoning_format == "effort":
            extra_body["effort"] = effort
        elif reasoning_format == "thinking":
            thinking = extra_body.get("thinking")
            if not isinstance(thinking, dict):
                thinking = {}
            thinking.setdefault("type", "adaptive")
            thinking["effort"] = effort
            extra_body["thinking"] = thinking
        else:
            extra_body["reasoning_effort"] = effort

    provider_extra_body = provider_cfg.get("extra_body", {})
    if isinstance(provider_extra_body, dict) and provider_extra_body:
        provider_extra_body = copy.deepcopy(provider_extra_body)
        deep_merge_dict(extra_body, provider_extra_body)

    return extra_body


def is_multimodal_content(content) -> bool:
    """Check if content is multimodal (list with image_url type)."""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for part in content
    )


def validate_messages(messages: List[dict]) -> List[dict]:
    """
    Validate and sanitize messages before sending to API.
    Ensures no malformed JSON or None values are sent.
    Preserves multimodal content (list format with images).
    """
    validated = []
    for msg in messages:
        # Ensure role exists and is valid
        role = msg.get("role", "user")
        if role not in ("system", "user", "assistant"):
            role = "user"

        content = msg.get("content")

        # Preserve multimodal content (list with image_url)
        if is_multimodal_content(content):
            # Validate each part of multimodal content
            validated_parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text = part.get("text", "")
                        # Keep text parts even if empty (some APIs need them)
                        validated_parts.append({"type": "text", "text": text.replace("\x00", "") if text else ""})
                    elif part.get("type") == "image_url":
                        validated_parts.append(part)
            # Ensure we have at least one text part
            if not any(p.get("type") == "text" for p in validated_parts):
                validated_parts.insert(0, {"type": "text", "text": ""})
            validated.append({"role": role, "content": validated_parts})
        else:
            # Standard string content
            if content is None:
                content = ""
            elif not isinstance(content, str):
                content = str(content)

            # Remove any null bytes or invalid characters
            content = content.replace("\x00", "")
            validated.append({"role": role, "content": content})

    return validated


def has_multimodal_message(messages: List[dict]) -> bool:
    """Check if any message contains multimodal content."""
    return any(is_multimodal_content(msg.get("content")) for msg in messages)


def strip_images_from_messages(messages: List[dict]) -> List[dict]:
    """Convert multimodal messages to text-only for non-vision providers."""
    stripped = []
    for msg in messages:
        content = msg.get("content")
        if is_multimodal_content(content):
            # Extract only text parts
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        text_parts.append(text)
            # Add note about omitted visual context
            text_parts.append("[Visual reference omitted for text-only model]")
            stripped.append({**msg, "content": "\n".join(text_parts)})
        else:
            stripped.append(dict(msg))
    return stripped


def format_as_single_user(messages: List[dict], system_prompt: str) -> List[dict]:
    """
    Format multi-message array as a single user message (SillyTavern style).

    Instead of sending:
        [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]

    We send everything as one user message:
        [{"role": "user", "content": "### Instructions\n...\nUser: ...\nAssistant: ..."}]

    This improves compatibility with various LLM backends and matches
    SillyTavern's "Single user message (no tools)" format.

    Note: Uses "Author: message" format (no brackets) to prevent LLMs from
    learning and outputting bracket patterns in their responses.

    Note: If messages contain multimodal content (images), this function
    returns None to signal that single-user mode should be bypassed.
    """
    # Check for multimodal content - cannot use single-user mode with images
    if has_multimodal_message(messages):
        return None

    parts = []

    # Add system prompt first
    if system_prompt and system_prompt.strip():
        parts.append(f"### Instructions\n{system_prompt.strip()}")

    # Add conversation messages, preserving the distinction between the main
    # system prompt and injected chatroom context when single-user mode flattens
    # everything into one message.
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            content = content.strip()

        if not content:
            continue

        if role == "system":
            section_title = "Context" if msg.get("kind") == "chatroom_context" else "Instructions"
            parts.append(f"### {section_title}\n{content}")
        elif role == "user":
            # Check if content already has Author: prefix (from format_history_split)
            # to avoid double-prefixing
            # Look for pattern like "Name: " at start (not brackets)
            if ": " in content[:50] and not content.startswith("("):
                first_colon = content.find(": ")
                prefix = content[:first_colon]
                # If prefix looks like a name (no special chars except spaces), it's already formatted
                if first_colon < 30 and prefix.replace(" ", "").isalnum():
                    parts.append(content)
                    continue
            # Add author prefix - use "author" key (not "author_name")
            author = msg.get("author", "User")
            parts.append(f"{author}: {content}")
        elif role == "assistant":
            # Bot's own messages - check for existing prefix
            if ": " in content[:50] and not content.startswith("("):
                first_colon = content.find(": ")
                prefix = content[:first_colon]
                if first_colon < 30 and prefix.replace(" ", "").isalnum():
                    parts.append(content)
                    continue
            # Always prefix assistant messages with author name
            author = msg.get("author", "Assistant")
            parts.append(f"{author}: {content}")
    
    # Combine all parts into a single user message
    combined = "\n\n".join(parts)
    
    return [{"role": "user", "content": combined}]


class AIProviderManager:
    """Manages multi-tier AI provider fallback with retry logic."""

    def __init__(self):
        self.providers: Dict[str, AsyncOpenAI] = {}
        self.image_providers: Dict[str, AsyncOpenAI] = {}
        self.status: Dict[str, str] = {tier: "unknown" for tier in PROVIDERS}
        self.image_status: Dict[str, str] = {tier: "unknown" for tier in IMAGE_PROVIDERS}
        self._vision_support_overrides: Dict[str, bool] = {}
        
        for tier, cfg in PROVIDERS.items():
            key = cfg.get("key")
            if key:
                # Auto-detect OpenRouter and inject recommended headers
                default_headers = {}
                if "openrouter.ai" in cfg.get("url", ""):
                    default_headers["HTTP-Referer"] = "https://github.com/TheLonelyDevil9/discord-pals"
                    default_headers["X-OpenRouter-Title"] = cfg.get("name", "Discord Pals")

                self.providers[tier] = AsyncOpenAI(
                    base_url=cfg["url"],
                    api_key=cfg["key"],
                    timeout=cfg.get("timeout") or API_TIMEOUT,
                    default_headers=default_headers or None
                )

        for tier, cfg in IMAGE_PROVIDERS.items():
            key = cfg.get("key")
            if key:
                default_headers = {}
                if "openrouter.ai" in cfg.get("url", ""):
                    default_headers["HTTP-Referer"] = "https://github.com/TheLonelyDevil9/discord-pals"
                    default_headers["X-OpenRouter-Title"] = cfg.get("name", "Discord Pals")
                self.image_providers[tier] = AsyncOpenAI(
                    base_url=cfg["url"],
                    api_key=key,
                    timeout=cfg.get("timeout") or API_TIMEOUT,
                    default_headers=default_headers or None,
                )

    def reload(self) -> None:
        """Rebuild provider clients after providers.json changes."""
        self.__init__()

    def _supports_vision_for_tier(self, tier: str) -> bool:
        """Resolve whether a tier should receive multimodal requests."""
        overrides = getattr(self, "_vision_support_overrides", {})
        if tier in overrides:
            return overrides[tier]
        return PROVIDERS[tier].get("supports_vision", True)

    def _looks_like_vision_rejection(self, error: Exception) -> bool:
        """Detect provider errors that mean image input is not actually supported."""
        parts = [str(error)]
        body = getattr(error, "body", None)
        if body:
            parts.append(str(body))

        text = " ".join(part.lower() for part in parts if part)
        hints = (
            "support image input",
            "image input",
            "images are not supported",
            "does not support images",
            "does not support image",
            "vision is not supported",
            "multimodal is not supported",
            "multimodal input",
        )
        return any(hint in text for hint in hints)

    def _build_tier_order(self, preferred_tier: str = "") -> List[str]:
        """Build the provider order for a request."""
        tier_order = list(PROVIDERS.keys())
        if preferred_tier and preferred_tier in tier_order:
            tier_order.remove(preferred_tier)
            tier_order.insert(0, preferred_tier)
        return tier_order

    def _build_image_tier_order(self, preferred_tier: str = "") -> List[str]:
        """Build the image provider order for a request."""
        tier_order = list(IMAGE_PROVIDERS.keys())
        if preferred_tier and preferred_tier in tier_order:
            tier_order.remove(preferred_tier)
            tier_order.insert(0, preferred_tier)
        return tier_order

    @staticmethod
    def _image_bytes_from_response(response) -> tuple[bytes | None, str | None]:
        """Return generated image bytes and revised prompt metadata when present."""
        image_items = getattr(response, "data", None) or []
        if not image_items:
            return None, None

        first_image = image_items[0]
        revised_prompt = getattr(first_image, "revised_prompt", None)
        b64_json = getattr(first_image, "b64_json", None)
        if b64_json:
            try:
                return base64.b64decode(b64_json), revised_prompt
            except (TypeError, ValueError):
                return None, revised_prompt

        url = getattr(first_image, "url", None)
        if isinstance(url, str) and url.startswith("data:image") and "," in url:
            try:
                return base64.b64decode(url.split(",", 1)[1]), revised_prompt
            except (TypeError, ValueError):
                return None, revised_prompt

        return None, revised_prompt

    async def generate_image(
        self,
        prompt: str,
        preferred_tier: str = "",
        req_id: str | None = None,
    ) -> dict | None:
        """Generate one image with configured image providers and return bytes metadata."""
        prompt = str(prompt or "").strip()
        if not prompt:
            return None

        req_id = req_id or log.new_request_id()
        for tier in self._build_image_tier_order(preferred_tier):
            if tier not in self.image_providers:
                self.image_status[tier] = "no key"
                continue

            cfg = IMAGE_PROVIDERS[tier]
            request_kwargs = {
                "model": cfg.get("model", "gpt-image-1"),
                "prompt": prompt,
                "n": 1,
                "size": cfg.get("size") or "1024x1024",
            }
            optional_fields = ("quality", "style", "response_format", "output_format", "background", "moderation")
            for field in optional_fields:
                if cfg.get(field):
                    request_kwargs[field] = cfg[field]
            if cfg.get("extra_body"):
                request_kwargs["extra_body"] = dict(cfg["extra_body"])

            try:
                started = time.perf_counter()
                response = await asyncio.wait_for(
                    self.image_providers[tier].images.generate(**request_kwargs),
                    timeout=cfg.get("timeout") or API_TIMEOUT,
                )
                image_bytes, revised_prompt = self._image_bytes_from_response(response)
                if not image_bytes:
                    self.image_status[tier] = "empty response"
                    continue

                self.image_status[tier] = "ok"
                log.ok(
                    f"[{tier}] Generated image with {cfg.get('model')}",
                    component="provider",
                    event="image_provider_response",
                    req_id=req_id,
                    tier=tier,
                    model=cfg.get("model"),
                    image_bytes=len(image_bytes),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                return {
                    "bytes": image_bytes,
                    "filename": "discord-pals-image.png",
                    "tier": tier,
                    "provider_name": cfg.get("name", tier),
                    "model": cfg.get("model"),
                    "revised_prompt": revised_prompt,
                }
            except asyncio.TimeoutError:
                self.image_status[tier] = "timeout"
                log.error(f"[{tier}] Image generation timed out", component="provider", event="image_provider_timeout", req_id=req_id, tier=tier)
                continue
            except Exception as e:
                self.image_status[tier] = "error"
                log.error(f"[{tier}] Image generation failed: {str(e)[:100]}", component="provider", event="image_provider_error", req_id=req_id, tier=tier, error_type=type(e).__name__)
                continue

        log.error("All image providers failed", component="provider", event="image_provider_all_failed", req_id=req_id)
        return None

    def can_use_vision(self, preferred_tier: str = "") -> bool:
        """Return True if any configured provider for this request supports vision."""
        for tier in self._build_tier_order(preferred_tier):
            if tier not in self.providers:
                continue
            if self._supports_vision_for_tier(tier):
                return True
        return False
    
    async def _try_generate(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: List[dict],
        temperature: float,
        max_tokens: int,
        tier: str,
        timeout: int = None,
        extra_body: Optional[dict] = None,
        include_body: str = "",
        exclude_body: str = "",
        include_headers: str = "",
        req_id: str | None = None,
        cycle: int = 0,
    ) -> str | None:
        """Try to generate with retries on rate limit."""

        for attempt in range(MAX_RETRIES):
            attempt_started = time.perf_counter()
            try:
                # Build request kwargs
                request_kwargs = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
                
                # Apply SillyTavern-style YAML parameters (preferred)
                if include_body:
                    merge_yaml_to_dict(request_kwargs, include_body)
                    log.debug(f"[{tier}] Applied include_body YAML", component="provider", event="provider_include_body", req_id=req_id, tier=tier)

                # Remove keys specified in exclude_body
                if exclude_body:
                    exclude_keys_by_yaml(request_kwargs, exclude_body)
                    log.debug(f"[{tier}] Applied exclude_body YAML", component="provider", event="provider_exclude_body", req_id=req_id, tier=tier)

                # Move SDK-passthrough keys from request_kwargs to extra_body
                # These are provider-specific params that OpenAI SDK doesn't recognize
                SDK_PASSTHROUGH_KEYS = {
                    'thinking', 'tools', 'tool_choice', 'response_format', 'chat_template_kwargs',
                    'reasoning', 'reasoning_effort', 'output_config', 'effort'
                }
                passthrough_params = {}
                for key in list(request_kwargs.keys()):
                    if key in SDK_PASSTHROUGH_KEYS:
                        passthrough_params[key] = request_kwargs.pop(key)

                # Merge passthrough params into extra_body
                if passthrough_params:
                    if extra_body:
                        extra_body = {**extra_body, **passthrough_params}
                    else:
                        extra_body = passthrough_params
                    log.debug(f"[{tier}] Moved to extra_body: {list(passthrough_params.keys())}", component="provider", event="provider_extra_body", req_id=req_id, tier=tier)

                # Pass extra_body as SDK parameter (bypasses validation)
                if extra_body:
                    request_kwargs["extra_body"] = extra_body
                    log.debug(
                        f"[{tier}] Using extra_body keys: {list(extra_body.keys())}",
                        component="provider",
                        event="provider_extra_body",
                        req_id=req_id,
                        tier=tier,
                    )

                # Parse and pass custom headers (YAML string -> dict)
                if include_headers and include_headers.strip():
                    if YAML_AVAILABLE:
                        try:
                            parsed = yaml.safe_load(include_headers)
                            if isinstance(parsed, dict):
                                request_kwargs["extra_headers"] = {str(k): str(v) for k, v in parsed.items()}
                                log.debug(f"[{tier}] Using extra_headers: {list(request_kwargs['extra_headers'].keys())}", component="provider", event="provider_headers", req_id=req_id, tier=tier)
                            elif isinstance(parsed, list):
                                headers = {}
                                for item in parsed:
                                    if isinstance(item, dict):
                                        headers.update({str(k): str(v) for k, v in item.items()})
                                if headers:
                                    request_kwargs["extra_headers"] = headers
                                    log.debug(f"[{tier}] Using extra_headers: {list(headers.keys())}", component="provider", event="provider_headers", req_id=req_id, tier=tier)
                        except Exception as e:
                            log.warn(f"[{tier}] Failed to parse include_headers YAML: {e}", component="provider", event="provider_headers_error", req_id=req_id, tier=tier)
                    else:
                        log.warn(f"[{tier}] include_headers configured but PyYAML not installed", component="provider", event="provider_headers_error", req_id=req_id, tier=tier)
                
                log.diagnostic(
                    f"[{tier}] Provider request",
                    component="provider",
                    event="provider_request",
                    req_id=req_id,
                    tier=tier,
                    model=model,
                    cycle=cycle,
                    attempt=attempt + 1,
                    message_count=len(messages),
                    max_tokens=max_tokens,
                    temperature=temperature,
                    has_extra_body=bool(extra_body),
                    include_body=bool(include_body),
                    exclude_body=bool(exclude_body),
                    include_headers=bool(include_headers),
                    timeout=timeout or API_TIMEOUT,
                )

                response = await asyncio.wait_for(
                    client.chat.completions.create(**request_kwargs),
                    timeout=timeout or API_TIMEOUT
                )
                latency_ms = int((time.perf_counter() - attempt_started) * 1000)
                
                # Check for valid response
                if response and response.choices and len(response.choices) > 0:
                    choice = response.choices[0]
                    content = choice.message.content
                    
                    # Strip reasoning_content if present (GLM thinking mode leaks)
                    if hasattr(choice.message, 'reasoning_content') and choice.message.reasoning_content:
                        log.debug(
                            f"[{tier}] Stripped {len(choice.message.reasoning_content)} chars of reasoning_content",
                            component="provider",
                            event="reasoning_content_stripped",
                            req_id=req_id,
                            tier=tier,
                            stripped_len=len(choice.message.reasoning_content),
                        )
                    
                    if not content or content.strip() == "":
                        # Detailed logging for empty responses
                        log.warn(
                            f"[{tier}] Empty content from {model}",
                            component="provider",
                            event="provider_empty",
                            req_id=req_id,
                            tier=tier,
                            model=model,
                            finish_reason=choice.finish_reason,
                            latency_ms=latency_ms,
                        )
                        if hasattr(choice.message, 'refusal') and choice.message.refusal:
                            log.warn(
                                f"[{tier}] Refusal: {choice.message.refusal}",
                                component="provider",
                                event="provider_refusal",
                                req_id=req_id,
                                tier=tier,
                            )
                        if extra_body:
                            log.warn(
                                f"[{tier}] extra_body keys were: {list(extra_body.keys())}",
                                component="provider",
                                event="provider_extra_body",
                                req_id=req_id,
                                tier=tier,
                            )
                        return None  # Return None to trigger fallback to next provider

                    usage = getattr(response, "usage", None)
                    log.ok(
                        f"[{tier}] Got {len(content)} chars from {model}",
                        component="provider",
                        event="provider_response",
                        req_id=req_id,
                        tier=tier,
                        model=model,
                        content_len=len(content),
                        latency_ms=latency_ms,
                        finish_reason=getattr(choice, "finish_reason", None),
                        prompt_tokens=getattr(usage, "prompt_tokens", None),
                        completion_tokens=getattr(usage, "completion_tokens", None),
                        total_tokens=getattr(usage, "total_tokens", None),
                        has_reasoning=bool(getattr(choice.message, "reasoning_content", None)),
                    )

                    # Raw generation logging (when enabled)
                    import runtime_config
                    if runtime_config.get("raw_generation_logging", False):
                        # Log in chunks to avoid overwhelming logs
                        preview_len = 1000
                        if len(content) <= preview_len:
                            log.info(f"[RAW-GEN] {log.redact(content)}", component="provider", event="raw_generation", req_id=req_id)
                        else:
                            log.info(
                                f"[RAW-GEN] {log.redact(content[:preview_len])}... ({len(content)} chars total)",
                                component="provider",
                                event="raw_generation",
                                req_id=req_id,
                            )

                    # Strip any reasoning/thinking content that leaked into output
                    content = remove_thinking_tags(content)

                    return content
                else:
                    log.warn(
                        f"[{tier}] No choices in response from {model}",
                        component="provider",
                        event="provider_no_choices",
                        req_id=req_id,
                        tier=tier,
                        model=model,
                        latency_ms=latency_ms,
                    )
                    if response:
                        log.warn(f"[{tier}] Response: {str(response)[:200]}", component="provider", event="provider_response_preview", req_id=req_id, tier=tier)
                return None
                
            except RateLimitError as e:
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    log.warn(
                        f"[{tier}] Rate limited, retrying in {delay}s...",
                        component="provider",
                        event="provider_retry",
                        req_id=req_id,
                        tier=tier,
                        attempt=attempt + 1,
                        delay_s=delay,
                        reason="rate_limit",
                    )
                    await asyncio.sleep(delay)
                else:
                    log.error(f"[{tier}] Rate limited, max retries exceeded", component="provider", event="provider_rate_limited", req_id=req_id, tier=tier)
                    raise
            except asyncio.TimeoutError:
                raise
            except APIError as e:
                if e.status_code == 429 and attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    log.warn(
                        f"[{tier}] Rate limited (429), retrying in {delay}s...",
                        component="provider",
                        event="provider_retry",
                        req_id=req_id,
                        tier=tier,
                        attempt=attempt + 1,
                        delay_s=delay,
                        reason="api_429",
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
        
        return None
    
    async def generate(
        self,
        messages: List[dict],
        system_prompt: str,
        temperature: float = None,
        max_tokens: int = None,
        use_single_user: bool = True,  # SillyTavern-style by default
        preferred_tier: str = "",  # Per-character provider preference
        req_id: str | None = None,
    ) -> str:
        """Generate response with automatic fallback and retry (3 full cycles).

        Args:
            messages: Conversation messages
            system_prompt: System prompt / character definition
            temperature: Response randomness (0-2). If None, uses per-provider config.
            max_tokens: Max response length. If None, uses per-provider config.
            use_single_user: If True (default), format as single user message
                             like SillyTavern's "Single user message (no tools)"
            preferred_tier: If set (primary/secondary/fallback), try this tier first
        """

        # Check if we have multimodal content
        has_images = has_multimodal_message(messages)

        # Format messages based on mode
        text_only_messages = None
        if use_single_user:
            # SillyTavern-style: combine everything into one user message
            # Returns None if messages contain multimodal content (images)
            full_messages = format_as_single_user(messages, system_prompt)
            if full_messages is None:
                # Multimodal content detected - fall back to multi-message format
                log.info("Multimodal content detected, using multi-message format")
                full_messages = [{"role": "system", "content": system_prompt}] + messages
                full_messages = validate_messages(full_messages)
                stripped_source_messages = strip_images_from_messages(messages)
                text_only_messages = format_as_single_user(stripped_source_messages, system_prompt)
                if text_only_messages is None:
                    text_only_messages = [{"role": "system", "content": system_prompt}] + stripped_source_messages
                    text_only_messages = validate_messages(text_only_messages)
        else:
            # Legacy multi-message format with roles
            full_messages = [{"role": "system", "content": system_prompt}] + messages
            full_messages = validate_messages(full_messages)
            if has_images:
                text_only_messages = strip_images_from_messages(full_messages)

        # Prepare text-only version for non-vision providers
        if has_images and text_only_messages is None:
            text_only_messages = strip_images_from_messages(full_messages)

        # Build tier order dynamically from all configured providers
        tier_order = self._build_tier_order(preferred_tier)
        req_id = req_id or log.new_request_id()
        if preferred_tier and tier_order and tier_order[0] == preferred_tier:
            log.info(f"Using preferred provider tier: {preferred_tier}", component="provider", event="provider_preferred", req_id=req_id)
        log.diagnostic(
            "Provider tier order built",
            component="provider",
            event="provider_tier_order",
            req_id=req_id,
            tier_order=tier_order,
            preferred_tier=preferred_tier,
            has_images=has_images,
            use_single_user=use_single_user,
        )

        # Retry all providers up to 3 full cycles
        for cycle in range(3):
            for tier in tier_order:
                if tier not in self.providers:
                    self.status[tier] = "no key"
                    continue

                model = PROVIDERS[tier]["model"]
                provider_cfg = PROVIDERS[tier]
                extra_body = build_reasoning_extra_body(provider_cfg)
                include_body = PROVIDERS[tier].get("include_body", "")
                exclude_body = PROVIDERS[tier].get("exclude_body", "")
                include_headers = PROVIDERS[tier].get("include_headers", "")

                # Merge OpenRouter-specific config into extra_body
                openrouter_cfg = PROVIDERS[tier].get("openrouter", {})
                if openrouter_cfg and "openrouter.ai" in PROVIDERS[tier].get("url", ""):
                    extra_body = {**extra_body, **openrouter_cfg} if extra_body else dict(openrouter_cfg)

                # Check if provider supports vision (default True, set false for text-only models)
                supports_vision = self._supports_vision_for_tier(tier)

                # Per-provider timeout (falls back to global API_TIMEOUT)
                effective_timeout = PROVIDERS[tier].get("timeout") or API_TIMEOUT

                # Use text-only messages for non-vision providers
                messages_to_send = full_messages
                if has_images and not supports_vision:
                    log.info(f"[{tier}] Provider doesn't support vision, using text-only", component="provider", event="vision_text_only", req_id=req_id, tier=tier, model=model)
                    messages_to_send = text_only_messages

                # Use per-provider settings, with fallback to function args or defaults
                provider_max_tokens = PROVIDERS[tier].get("max_tokens", 8192)
                provider_temperature = PROVIDERS[tier].get("temperature", 1.0)

                # Allow function args to override provider config
                effective_max_tokens = max_tokens if max_tokens is not None else provider_max_tokens
                effective_temperature = temperature if temperature is not None else provider_temperature

                try:
                    client = self.providers[tier]
                    result = await self._try_generate(
                        client, model, messages_to_send, effective_temperature, effective_max_tokens, tier,
                        timeout=effective_timeout,
                        extra_body=extra_body if extra_body else None,
                        include_body=include_body,
                        exclude_body=exclude_body,
                        include_headers=include_headers,
                        req_id=req_id,
                        cycle=cycle + 1,
                    )
                    
                    if result:
                        self.status[tier] = "ok"
                        return result
                    else:
                        self.status[tier] = "empty response"
                        continue
                    
                except asyncio.TimeoutError:
                    self.status[tier] = "timeout"
                    log.error(f"[{tier}] Timeout after {effective_timeout}s", component="provider", event="provider_timeout", req_id=req_id, tier=tier, model=model, timeout=effective_timeout)
                    continue
                except RateLimitError:
                    self.status[tier] = "rate limited"
                    continue
                except APIError as e:
                    if has_images and supports_vision and text_only_messages and self._looks_like_vision_rejection(e):
                        log.warn(f"[{tier}] Vision input rejected by provider, retrying as text-only", component="provider", event="vision_fallback", req_id=req_id, tier=tier, model=model)
                        self._vision_support_overrides[tier] = False
                        try:
                            result = await self._try_generate(
                                client, model, text_only_messages, effective_temperature, effective_max_tokens, tier,
                                timeout=effective_timeout,
                                extra_body=extra_body if extra_body else None,
                                include_body=include_body,
                                exclude_body=exclude_body,
                                include_headers=include_headers,
                                req_id=req_id,
                                cycle=cycle + 1,
                            )
                            if result:
                                self.status[tier] = "ok"
                                return result
                            self.status[tier] = "empty response"
                            continue
                        except Exception as fallback_error:
                            self.status[tier] = "error"
                            log.error(f"[{tier}] text-only fallback failed: {str(fallback_error)[:100]}", component="provider", event="provider_error", req_id=req_id, tier=tier, model=model)
                            continue

                    self.status[tier] = "error"
                    log.error(f"[{tier}] {str(e)[:100]}", component="provider", event="provider_error", req_id=req_id, tier=tier, model=model, error_type=type(e).__name__)
                    continue
                except Exception as e:
                    if has_images and supports_vision and text_only_messages and self._looks_like_vision_rejection(e):
                        log.warn(f"[{tier}] Vision input rejected by provider, retrying as text-only", component="provider", event="vision_fallback", req_id=req_id, tier=tier, model=model)
                        self._vision_support_overrides[tier] = False
                        try:
                            result = await self._try_generate(
                                client, model, text_only_messages, effective_temperature, effective_max_tokens, tier,
                                timeout=effective_timeout,
                                extra_body=extra_body if extra_body else None,
                                include_body=include_body,
                                exclude_body=exclude_body,
                                include_headers=include_headers,
                                req_id=req_id,
                                cycle=cycle + 1,
                            )
                            if result:
                                self.status[tier] = "ok"
                                return result
                            self.status[tier] = "empty response"
                            continue
                        except Exception as fallback_error:
                            self.status[tier] = "error"
                            log.error(f"[{tier}] text-only fallback failed: {str(fallback_error)[:100]}", component="provider", event="provider_error", req_id=req_id, tier=tier, model=model)
                            continue

                    self.status[tier] = "error"
                    log.error(f"[{tier}] {str(e)[:100]}", component="provider", event="provider_error", req_id=req_id, tier=tier, model=model, error_type=type(e).__name__)
                    continue
            
            # Wait before next cycle
            if cycle < 2:
                await asyncio.sleep(2)
        
        # Silent fail - no public error message
        log.error("All providers failed after 3 cycles", component="provider", event="provider_all_failed", req_id=req_id)
        return None
    
    def get_status(self) -> str:
        """Get formatted status of all providers."""
        lines = ["**Provider Status:**"]
        for tier in PROVIDERS.keys():
            name = PROVIDERS[tier]["name"]
            status = self.status.get(tier, "unknown")
            emoji = "✅" if status == "ok" else "❓" if status == "unknown" else "❌"
            lines.append(f"• {name}: {emoji} {status}")
        return "\n".join(lines)

    async def get_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding vector for text using OpenAI-compatible API.

        Uses the primary provider's embedding endpoint.
        Returns None if embedding fails (graceful degradation).
        """
        if not text or not text.strip():
            return None

        # Try providers in order until one works
        for tier in PROVIDERS.keys():
            if tier not in self.providers:
                continue

            # Check if provider has embedding support configured
            provider_cfg = PROVIDERS.get(tier, {})
            embedding_model = provider_cfg.get("embedding_model", "text-embedding-3-small")

            # Skip if provider explicitly disabled embeddings
            if embedding_model is None or embedding_model == "":
                continue

            try:
                client = self.providers[tier]
                response = await asyncio.wait_for(
                    client.embeddings.create(
                        model=embedding_model,
                        input=text[:8000]  # Truncate to avoid token limits
                    ),
                    timeout=30  # Shorter timeout for embeddings
                )

                if response and response.data and len(response.data) > 0:
                    embedding = response.data[0].embedding
                    log.debug(f"[{tier}] Generated embedding ({len(embedding)} dims) for: {text[:50]}...")
                    return embedding

            except Exception as e:
                log.debug(f"[{tier}] Embedding generation failed: {e}")
                continue

        return None


# Global instance
provider_manager = AIProviderManager()

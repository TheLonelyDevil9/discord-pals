"""
Discord Pals - AI Providers
3-tier fallback system using OpenAI-compatible API with rate limit handling.
"""

from openai import AsyncOpenAI, RateLimitError, APIError
from typing import List, Dict, Optional
from config import PROVIDERS, API_TIMEOUT
import asyncio
import logger as log


MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff: 1s, 2s, 4s


def validate_messages(messages: List[dict]) -> List[dict]:
    """
    Validate and sanitize messages before sending to API.
    Ensures no malformed JSON or None values are sent.
    """
    validated = []
    for msg in messages:
        # Ensure role exists and is valid
        role = msg.get("role", "user")
        if role not in ("system", "user", "assistant"):
            role = "user"
        
        # Ensure content is a string, never None
        content = msg.get("content")
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = str(content)
        
        # Remove any null bytes or invalid characters
        content = content.replace("\x00", "")
        
        validated.append({"role": role, "content": content})
    
    return validated


def format_as_single_user(messages: List[dict], system_prompt: str) -> List[dict]:
    """
    Format multi-message array as a single user message (SillyTavern style).
    
    Instead of sending:
        [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]
    
    We send everything as one user message:
        [{"role": "user", "content": "[System]\n...\n[User]\n...\n[Assistant]\n..."}]
    
    This improves compatibility with various LLM backends and matches
    SillyTavern's "Single user message (no tools)" format.
    """
    parts = []
    
    # Add system prompt first
    if system_prompt and system_prompt.strip():
        parts.append(f"[System]\n{system_prompt.strip()}")
    
    # Add conversation messages
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "").strip()
        
        if not content:
            continue
        
        if role == "system":
            parts.append(f"[System]\n{content}")
        elif role == "user":
            # Check if content already has [Author]: prefix (from format_history_split)
            # to avoid double-prefixing like "[User]\n[Alice]: Hello"
            if content.startswith("[") and "]: " in content[:50]:
                # Already prefixed, just add as-is
                parts.append(content)
            else:
                # Add author prefix - use "author" key (not "author_name")
                author = msg.get("author", "User")
                parts.append(f"[{author}]: {content}")
        elif role == "assistant":
            # Bot's own messages - check for existing prefix
            if content.startswith("[") and "]: " in content[:50]:
                parts.append(content)
            else:
                author = msg.get("author", "Assistant")
                parts.append(f"[{author}]: {content}")
    
    # Combine all parts into a single user message
    combined = "\n\n".join(parts)
    
    return [{"role": "user", "content": combined}]


class AIProviderManager:
    """Manages 3-tier AI provider fallback with retry logic."""
    
    def __init__(self):
        self.providers: Dict[str, AsyncOpenAI] = {}
        self.status: Dict[str, str] = {"primary": "unknown", "secondary": "unknown", "fallback": "unknown"}
        
        for tier, cfg in PROVIDERS.items():
            key = cfg.get("key")
            if key:
                self.providers[tier] = AsyncOpenAI(
                    base_url=cfg["url"],
                    api_key=cfg["key"],
                    timeout=API_TIMEOUT
                )
    
    async def _try_generate(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: List[dict],
        temperature: float,
        max_tokens: int,
        tier: str,
        extra_body: Optional[dict] = None
    ) -> str | None:
        """Try to generate with retries on rate limit."""
        
        for attempt in range(MAX_RETRIES):
            try:
                # Build request kwargs
                request_kwargs = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
                
                # Add extra_body if provided (for provider-specific options)
                if extra_body:
                    request_kwargs["extra_body"] = extra_body
                    log.debug(f"[{tier}] Using extra_body: {extra_body}")
                
                log.debug(f"[{tier}] Requesting {model} with {len(messages)} messages, max_tokens={max_tokens}")
                
                response = await asyncio.wait_for(
                    client.chat.completions.create(**request_kwargs),
                    timeout=API_TIMEOUT
                )
                
                # Check for valid response
                if response and response.choices and len(response.choices) > 0:
                    choice = response.choices[0]
                    content = choice.message.content
                    
                    if not content or content.strip() == "":
                        # Detailed logging for empty responses
                        log.warn(f"[{tier}] Empty content from {model}")
                        log.warn(f"[{tier}] finish_reason={choice.finish_reason}")
                        if hasattr(choice.message, 'refusal') and choice.message.refusal:
                            log.warn(f"[{tier}] Refusal: {choice.message.refusal}")
                        if extra_body:
                            log.warn(f"[{tier}] extra_body was: {extra_body}")
                        return "..."
                    
                    log.ok(f"[{tier}] Got {len(content)} chars from {model}")
                    return content
                else:
                    log.warn(f"[{tier}] No choices in response from {model}")
                    if response:
                        log.warn(f"[{tier}] Response: {str(response)[:200]}")
                return None
                
            except RateLimitError as e:
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    log.warn(f"[{tier}] Rate limited, retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    log.error(f"[{tier}] Rate limited, max retries exceeded")
                    raise
            except asyncio.TimeoutError:
                raise
            except APIError as e:
                if e.status_code == 429 and attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    log.warn(f"[{tier}] Rate limited (429), retrying in {delay}s...")
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
        use_single_user: bool = True  # SillyTavern-style by default
    ) -> str:
        """Generate response with automatic fallback and retry (3 full cycles).
        
        Args:
            messages: Conversation messages
            system_prompt: System prompt / character definition
            temperature: Response randomness (0-2). If None, uses per-provider config.
            max_tokens: Max response length. If None, uses per-provider config.
            use_single_user: If True (default), format as single user message
                             like SillyTavern's "Single user message (no tools)"
        """
        
        # Format messages based on mode
        if use_single_user:
            # SillyTavern-style: combine everything into one user message
            full_messages = format_as_single_user(messages, system_prompt)
        else:
            # Legacy multi-message format with roles
            full_messages = [{"role": "system", "content": system_prompt}] + messages
            full_messages = validate_messages(full_messages)
        
        # Retry all providers up to 3 full cycles
        for cycle in range(3):
            for tier in ["primary", "secondary", "fallback"]:
                if tier not in self.providers:
                    self.status[tier] = "no key"
                    continue
                
                model = PROVIDERS[tier]["model"]
                extra_body = PROVIDERS[tier].get("extra_body", {})
                
                # Use per-provider settings, with fallback to function args or defaults
                provider_max_tokens = PROVIDERS[tier].get("max_tokens", 8192)
                provider_temperature = PROVIDERS[tier].get("temperature", 1.0)
                
                # Allow function args to override provider config
                effective_max_tokens = max_tokens if max_tokens is not None else provider_max_tokens
                effective_temperature = temperature if temperature is not None else provider_temperature
                    
                try:
                    client = self.providers[tier]
                    result = await self._try_generate(
                        client, model, full_messages, effective_temperature, effective_max_tokens, tier,
                        extra_body=extra_body if extra_body else None
                    )
                    
                    if result:
                        self.status[tier] = "ok"
                        return result
                    else:
                        self.status[tier] = "empty response"
                        continue
                    
                except asyncio.TimeoutError:
                    self.status[tier] = "timeout"
                    log.error(f"[{tier}] Timeout after {API_TIMEOUT}s")
                    continue
                except RateLimitError:
                    self.status[tier] = "rate limited"
                    continue
                except Exception as e:
                    self.status[tier] = "error"
                    log.error(f"[{tier}] {str(e)[:100]}")
                    continue
            
            # Wait before next cycle
            if cycle < 2:
                await asyncio.sleep(2)
        
        # Silent fail - no public error message
        log.error("All providers failed after 3 cycles")
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


# Global instance
provider_manager = AIProviderManager()

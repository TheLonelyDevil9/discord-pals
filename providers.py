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
                
                response = await asyncio.wait_for(
                    client.chat.completions.create(**request_kwargs),
                    timeout=API_TIMEOUT
                )
                
                # Check for valid response
                if response and response.choices and len(response.choices) > 0:
                    content = response.choices[0].message.content
                    return content if content else "..."
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
        temperature: float = 1.0,
        max_tokens: int = 2000
    ) -> str:
        """Generate response with automatic fallback and retry (3 full cycles)."""
        
        # Validate and sanitize all messages before sending
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
                    
                try:
                    client = self.providers[tier]
                    result = await self._try_generate(
                        client, model, full_messages, temperature, max_tokens, tier,
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

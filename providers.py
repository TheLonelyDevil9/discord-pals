"""
Discord Pals - AI Providers
3-tier fallback system using OpenAI-compatible API.
"""

from openai import AsyncOpenAI
from typing import List, Optional
from config import PROVIDERS, API_TIMEOUT
import asyncio
import logger as log


class AIProviderManager:
    """Manages 3-tier AI provider fallback."""
    
    def __init__(self):
        self.providers = {}
        self.status = {"primary": "unknown", "secondary": "unknown", "fallback": "unknown"}
        
        for tier, cfg in PROVIDERS.items():
            key = cfg.get("key")
            if key:
                self.providers[tier] = AsyncOpenAI(
                    base_url=cfg["url"],
                    api_key=cfg["key"],
                    timeout=API_TIMEOUT
                )
    
    async def generate(
        self,
        messages: List[dict],
        system_prompt: str,
        temperature: float = 1.0,
        max_tokens: int = 2000
    ) -> str:
        """Generate response with automatic fallback."""
        
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        
        for tier in ["primary", "secondary", "fallback"]:
            if tier not in self.providers:
                self.status[tier] = "no key"
                continue
            
            provider_name = PROVIDERS[tier].get("name", tier)
            model = PROVIDERS[tier]["model"]
                
            try:
                client = self.providers[tier]
                
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=full_messages,
                        temperature=temperature,
                        max_tokens=max_tokens
                    ),
                    timeout=API_TIMEOUT
                )
                
                # Check for valid response
                if response and response.choices and len(response.choices) > 0:
                    content = response.choices[0].message.content
                    self.status[tier] = "ok"
                    return content if content else "..."
                else:
                    self.status[tier] = "empty response"
                    continue
                
            except asyncio.TimeoutError:
                self.status[tier] = "timeout"
                log.error(f"[{tier}] Timeout after {API_TIMEOUT}s")
                continue
            except Exception as e:
                self.status[tier] = f"error"
                log.error(f"[{tier}] {str(e)[:100]}")
                continue
        
        log.error("All providers failed")
        return "❌ All providers failed. Please try again later."
    
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

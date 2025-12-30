"""
Discord Pals - AI Providers
3-tier fallback system using OpenAI-compatible API.
"""

from openai import AsyncOpenAI
from typing import List, Optional
from config import PROVIDERS, API_TIMEOUT
import asyncio
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("providers")


class AIProviderManager:
    """Manages 3-tier AI provider fallback."""
    
    def __init__(self):
        self.providers = {}
        self.status = {"primary": "unknown", "secondary": "unknown", "fallback": "unknown"}
        
        logger.info("=" * 50)
        logger.info("Initializing AIProviderManager")
        logger.info(f"Configured providers: {list(PROVIDERS.keys())}")
        logger.info(f"API Timeout: {API_TIMEOUT}s")
        
        for tier, cfg in PROVIDERS.items():
            logger.debug(f"[{tier}] name={cfg.get('name')} url={cfg.get('url')} model={cfg.get('model')}")
            key = cfg.get("key")
            if key:
                logger.info(f"[{tier}] ✓ API key present (len={len(key)}), creating client")
                self.providers[tier] = AsyncOpenAI(
                    base_url=cfg["url"],
                    api_key=cfg["key"],
                    timeout=API_TIMEOUT
                )
            else:
                logger.warning(f"[{tier}] ✗ No API key set - provider will be skipped")
    
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
                logger.debug(f"[{tier}] Skipped - no provider configured")
                continue
            
            provider_name = PROVIDERS[tier].get("name", tier)
            model = PROVIDERS[tier]["model"]
            url = PROVIDERS[tier]["url"]
            logger.info(f"[{tier}] Attempting {provider_name} | model={model} | url={url}")
                
            try:
                client = self.providers[tier]
                
                logger.debug(f"[{tier}] Sending request with {len(full_messages)} messages, temp={temperature}, max_tokens={max_tokens}")
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=full_messages,
                        temperature=temperature,
                        max_tokens=max_tokens
                    ),
                    timeout=API_TIMEOUT
                )
                
                self.status[tier] = "ok"
                content = response.choices[0].message.content
                logger.info(f"[{tier}] ✓ Success! Response length: {len(content) if content else 0} chars")
                return content if content else "..."
                
            except asyncio.TimeoutError:
                self.status[tier] = "timeout"
                logger.error(f"[{tier}] ✗ TIMEOUT after {API_TIMEOUT}s")
                continue
            except Exception as e:
                error_msg = str(e)
                self.status[tier] = f"error: {error_msg[:50]}"
                logger.error(f"[{tier}] ✗ ERROR: {error_msg}")
                logger.exception(f"[{tier}] Full traceback:")
                continue
        
        logger.error("=" * 50)
        logger.error("ALL PROVIDERS FAILED - No response generated")
        logger.error(f"Final status: {self.status}")
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

"""Provider gateway facade.

This module is a strangler seam over the existing provider manager. It keeps
legacy runtime behavior intact while giving new provider adapters one place to
land behind a small interface.
"""

from __future__ import annotations

from typing import List

from providers import (
    build_legacy_chat_request_kwargs,
    provider_manager as legacy_provider_manager,
)


class ProviderGateway:
    """Facade for provider runtime calls.

    The first implementation delegates directly to the legacy
    ``AIProviderManager`` so provider order, fallback, request bodies, and
    diagnostics remain unchanged.
    """

    def __init__(self, legacy_manager=None):
        self.legacy_manager = legacy_manager or legacy_provider_manager

    @staticmethod
    def build_chat_completion_kwargs(**kwargs):
        return build_legacy_chat_request_kwargs(**kwargs)

    async def generate(
        self,
        messages: List[dict],
        system_prompt: str,
        temperature: float = None,
        max_tokens: int = None,
        use_single_user: bool = True,
        preferred_tier: str = "",
        req_id: str | None = None,
    ) -> str | None:
        return await self.legacy_manager.generate(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            use_single_user=use_single_user,
            preferred_tier=preferred_tier,
            req_id=req_id,
        )

    async def generate_text(self, *args, **kwargs) -> str | None:
        return await self.generate(*args, **kwargs)

    async def generate_image(
        self,
        prompt: str,
        preferred_tier: str = "",
        req_id: str | None = None,
    ) -> dict | None:
        return await self.legacy_manager.generate_image(
            prompt,
            preferred_tier=preferred_tier,
            req_id=req_id,
        )

    async def get_embedding(self, text: str):
        return await self.legacy_manager.get_embedding(text)

    def can_use_vision(self, preferred_tier: str = "") -> bool:
        return self.legacy_manager.can_use_vision(preferred_tier)

    def get_status(self) -> str:
        return self.legacy_manager.get_status()

    def reload(self) -> None:
        self.legacy_manager.reload()


provider_gateway = ProviderGateway()

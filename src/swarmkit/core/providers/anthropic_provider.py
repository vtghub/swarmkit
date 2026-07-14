from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from swarmkit.core.providers.base import Provider, ProviderResponse


class AnthropicProvider(Provider):
    """Wraps AsyncAnthropic. Adaptive thinking + output_config.effort on every
    call; no manual thinking budgets (removed on current-generation models)."""

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        self.client = client or AsyncAnthropic()

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        effort: str = "medium",
        response_schema: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        output_config: dict[str, Any] = {"effort": effort}
        if response_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": response_schema}
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 8192,
            "system": system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": output_config,
        }
        if tools:
            kwargs["tools"] = tools
        response = await self.client.messages.create(**kwargs)
        text = next((b.text for b in response.content if b.type == "text"), "")
        return ProviderResponse(
            request_id=response._request_id,
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            raw=response,
        )

    async def count_tokens(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        kwargs: dict[str, Any] = {"model": model, "system": system, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        result = await self.client.messages.count_tokens(**kwargs)
        return result.input_tokens

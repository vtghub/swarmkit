"""Token counting via the Anthropic API's own endpoint — never a local tokenizer
approximation (tiktoken and friends undercount Claude tokens significantly).
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic


async def count_tokens(
    client: AsyncAnthropic,
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    kwargs: dict[str, Any] = {"model": model, "system": system, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    result = await client.messages.count_tokens(**kwargs)
    return result.input_tokens

"""Provider abstraction. Kept intentionally thin: I/O-bound network calls stay in
Python, where the Anthropic SDK's own tool-runner ergonomics are worth keeping.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderResponse:
    """A single completion, with the real usage/id fields needed to prove a
    request actually happened (used by the Phase 0 real-provider proof tests)."""

    request_id: str | None
    text: str
    input_tokens: int
    output_tokens: int
    raw: Any = field(repr=False, default=None)


class Provider(ABC):
    """One LLM backend. `AnthropicProvider` is the only implementation for now;
    this ABC exists so a second provider can be added without touching agents/swarm.
    """

    @abstractmethod
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
        """Run one non-streaming completion and return it. `response_schema`,
        when given, constrains the response to that JSON schema
        (`output_config.format`) — used by the swarm coordinator's subtask
        decomposition so its output is guaranteed-parseable, not regex-scraped
        out of free text."""

    @abstractmethod
    async def count_tokens(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        """Return the exact input token count for a would-be request, via the
        provider's own counting endpoint — never a local tokenizer approximation.
        """

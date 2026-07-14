"""AgentCatalog: the coordinator-facing view of available agents.

Only `list_summaries()` — name + description — is meant to ever enter an
LLM's context. `load()` (full system prompt, allowed tools, model, effort)
is called only at actual agent spawn time. See agents/loader.py for the
file-discovery mechanics and override precedence.
"""

from __future__ import annotations

from pathlib import Path

from swarmkit.agents import loader
from swarmkit.agents.loader import AgentDefinition, AgentSummary

__all__ = ["AgentCatalog", "AgentDefinition", "AgentSummary"]


class AgentCatalog:
    def __init__(self, extra_dirs: list[Path] | None = None) -> None:
        self._extra_dirs = extra_dirs

    def list_summaries(self) -> list[AgentSummary]:
        return loader.list_summaries(self._extra_dirs)

    def load(self, name: str) -> AgentDefinition:
        return loader.load(name, self._extra_dirs)

    def render_summary(self) -> str:
        """Compact text listing for a coordinator's system prompt — the only
        representation of the catalog that should ever reach an LLM's
        context. It structurally cannot include a system_prompt or tool list."""
        return "\n".join(f"- {s.name}: {s.description}" for s in self.list_summaries())

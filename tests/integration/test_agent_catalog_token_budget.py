"""Live token-budget proof: the agent catalog summary stays a few thousand
tokens even as more definitions are added, using the real Anthropic
count_tokens endpoint (never a local tokenizer approximation). Skipped
without a real credential."""

from __future__ import annotations

import os

import pytest
import yaml
from anthropic import AsyncAnthropic

from swarmkit.agents.catalog import AgentCatalog

pytestmark = pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
    reason="requires a real Anthropic credential",
)

MODEL = "claude-haiku-4-5"
TOKEN_BUDGET = 3000  # generous ceiling; the 5-agent builtin catalog uses far less


async def test_catalog_summary_stays_within_a_small_token_budget():
    client = AsyncAnthropic()
    catalog = AgentCatalog()
    summary = catalog.render_summary()

    result = await client.messages.count_tokens(
        model=MODEL,
        messages=[{"role": "user", "content": summary}],
    )
    assert result.input_tokens < TOKEN_BUDGET


async def test_adding_many_bloated_agents_does_not_blow_the_token_budget(tmp_path):
    """Simulates a much larger personal agent library — since only
    name+description enters the summary, even 50 additional agents with huge
    personas shouldn't meaningfully move the token budget."""
    client = AsyncAnthropic()
    for i in range(50):
        definition = {
            "name": f"custom-{i}",
            "description": f"A custom test agent number {i} for token-budget verification.",
            "system_prompt": "x" * 5000,  # a large persona that must NOT leak into the summary
            "allowed_tools": ["run_command"],
            "default_model": "claude-haiku-4-5",
            "default_effort": "low",
        }
        (tmp_path / f"custom-{i}.yaml").write_text(yaml.safe_dump(definition))

    catalog = AgentCatalog(extra_dirs=[tmp_path])
    summary = catalog.render_summary()

    result = await client.messages.count_tokens(
        model=MODEL,
        messages=[{"role": "user", "content": summary}],
    )
    assert result.input_tokens < TOKEN_BUDGET

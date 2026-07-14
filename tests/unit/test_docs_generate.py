"""Proves docs/generate.py emits a lean, non-boilerplate AGENTS.md/CLAUDE.md
whose agent-catalog section is rendered from the live catalog (so it never
balloons past what's actually installed, regardless of catalog size) and
that both files are byte-identical."""

from __future__ import annotations

import yaml

from swarmkit.agents.catalog import AgentCatalog
from swarmkit.docs.generate import generate, write

LINE_BUDGET = 200  # generous ceiling; the real v1 doc is well under 100 lines


def test_generated_doc_stays_within_a_small_line_budget():
    content = generate()
    assert len(content.splitlines()) < LINE_BUDGET


def test_generated_doc_lists_the_builtin_catalog_by_name():
    content = generate()
    catalog = AgentCatalog()
    for summary in catalog.list_summaries():
        assert summary.name in content
        assert summary.description in content


def test_generated_doc_never_leaks_a_system_prompt(tmp_path):
    huge_persona = "x" * 5000
    (tmp_path / "custom.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "custom",
                "description": "A custom test agent.",
                "system_prompt": huge_persona,
                "allowed_tools": ["run_command"],
                "default_model": "claude-haiku-4-5",
                "default_effort": "low",
            }
        )
    )
    catalog = AgentCatalog(extra_dirs=[tmp_path])
    content = generate(catalog)
    assert "custom" in content
    assert huge_persona not in content


def test_generated_doc_covers_every_v1_capability():
    content = generate()
    for keyword in (
        "swarmkit run",
        "swarm run",
        "Agent catalog",
        "Swarm coordination",
        "Memory / RAG",
        "MCP integration",
        "Self-learning",
        "trajectories",
        "Security & federation",
        "audit",
        "peer add",
        "identity",
    ):
        assert keyword in content


def test_write_produces_identical_agents_and_claude_files(tmp_path):
    agents_path, claude_path = write(tmp_path)
    assert agents_path == tmp_path / "AGENTS.md"
    assert claude_path == tmp_path / "CLAUDE.md"
    assert agents_path.read_text() == claude_path.read_text()
    assert agents_path.read_text() == generate()

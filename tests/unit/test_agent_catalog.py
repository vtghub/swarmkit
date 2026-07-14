"""Agent catalog laziness: the coordinator's context only ever sees
name+description. This is a structural guarantee (AgentSummary has no
system_prompt/allowed_tools fields at all), not just a token-count
convention, so the catalog's context footprint stays small regardless of
how many agents are defined.
"""

from __future__ import annotations

import dataclasses

import pytest
import yaml

from swarmkit.agents.catalog import AgentCatalog
from swarmkit.agents.loader import AgentSummary

BUILTIN_NAMES = {"coder", "reviewer", "tester", "docs", "architect"}


def test_builtin_catalog_has_the_five_starter_agents():
    catalog = AgentCatalog()
    names = {s.name for s in catalog.list_summaries()}
    assert BUILTIN_NAMES <= names


def test_summary_structurally_cannot_carry_a_system_prompt():
    field_names = {f.name for f in dataclasses.fields(AgentSummary)}
    assert field_names == {"name", "description"}


def test_render_summary_excludes_full_persona_text():
    catalog = AgentCatalog()
    summary_text = catalog.render_summary()
    for name in BUILTIN_NAMES:
        definition = catalog.load(name)
        # The full system prompt is much longer than a description and should
        # never appear verbatim in the compact summary the coordinator sees.
        assert definition.system_prompt not in summary_text


def test_load_returns_the_full_definition():
    catalog = AgentCatalog()
    definition = catalog.load("coder")
    assert definition.name == "coder"
    assert definition.system_prompt.strip()
    assert "run_command" in definition.allowed_tools
    assert definition.default_model
    assert definition.default_effort


def test_load_unknown_agent_raises():
    catalog = AgentCatalog()
    with pytest.raises(KeyError):
        catalog.load("does-not-exist")


def test_extra_dir_definition_overrides_a_builtin_of_the_same_name(tmp_path):
    override = {
        "name": "coder",
        "description": "A custom coder override for this test.",
        "system_prompt": "You are a custom test coder.",
        "allowed_tools": ["run_command"],
        "default_model": "claude-haiku-4-5",
        "default_effort": "low",
    }
    (tmp_path / "coder.yaml").write_text(yaml.safe_dump(override))

    catalog = AgentCatalog(extra_dirs=[tmp_path])
    definition = catalog.load("coder")
    assert definition.description == "A custom coder override for this test."
    assert definition.default_model == "claude-haiku-4-5"

    summaries = {s.name: s for s in catalog.list_summaries()}
    assert summaries["coder"].description == "A custom coder override for this test."

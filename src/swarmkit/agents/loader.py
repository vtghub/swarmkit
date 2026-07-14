"""Discovers agent YAML definitions from built-in and user directories.

Lazy loading is structural, not just a token-counting convention:
`list_summaries` parses each file but returns only `AgentSummary` (name +
description) — there is no code path from a summary back to
`system_prompt`/`allowed_tools` without a separate `load()` call. This keeps
the catalog's context footprint small and testable regardless of how many
agents are defined.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

BUILTIN_DEFINITIONS_DIR = Path(__file__).parent / "definitions"


@dataclass(frozen=True)
class AgentSummary:
    name: str
    description: str


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    system_prompt: str
    allowed_tools: list[str]
    default_model: str
    default_effort: str


def _user_definitions_dir() -> Path:
    override = os.environ.get("SWARMKIT_AGENTS_DIR")
    if override:
        return Path(override)
    return Path.home() / ".config" / "swarmkit" / "agents"


def _definition_dirs(extra_dirs: list[Path] | None = None) -> list[Path]:
    # Later directories win on a name collision — user/extra definitions can
    # override a built-in agent of the same name.
    dirs = [BUILTIN_DEFINITIONS_DIR, _user_definitions_dir(), *(extra_dirs or [])]
    return [d for d in dirs if d.is_dir()]


def _load_all_raw(extra_dirs: list[Path] | None = None) -> dict[str, dict]:
    raw_by_name: dict[str, dict] = {}
    for directory in _definition_dirs(extra_dirs):
        for path in sorted(directory.glob("*.yaml")):
            with path.open() as f:
                raw = yaml.safe_load(f)
            raw_by_name[raw["name"]] = raw
    return raw_by_name


def list_summaries(extra_dirs: list[Path] | None = None) -> list[AgentSummary]:
    return [
        AgentSummary(name=raw["name"], description=raw["description"])
        for raw in _load_all_raw(extra_dirs).values()
    ]


def load(name: str, extra_dirs: list[Path] | None = None) -> AgentDefinition:
    raw_by_name = _load_all_raw(extra_dirs)
    if name not in raw_by_name:
        raise KeyError(f"no agent definition named {name!r}")
    raw = raw_by_name[name]
    return AgentDefinition(
        name=raw["name"],
        description=raw["description"],
        system_prompt=raw["system_prompt"],
        allowed_tools=raw.get("allowed_tools", []),
        default_model=raw.get("default_model", "claude-sonnet-5"),
        default_effort=raw.get("default_effort", "medium"),
    )

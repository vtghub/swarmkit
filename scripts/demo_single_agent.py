#!/usr/bin/env python3
"""Phase 0 smoke test: a real Anthropic call whose tool use runs as a real
Rust-sandboxed subprocess. Requires ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN).

    python scripts/demo_single_agent.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

from swarmkit.agents.base import Agent, AgentConfig


async def main() -> None:
    with tempfile.TemporaryDirectory() as workdir:
        config = AgentConfig(
            name="demo-agent",
            model="claude-haiku-4-5",
            system_prompt=(
                "Use the run_command tool to run exactly one echo command, then "
                "report its output in one sentence."
            ),
        )
        agent = Agent(config)
        result = await agent.run(
            'Run `echo hello-from-swarmkit` with the run_command tool and tell me what it printed.',
            jail_root=workdir,
            workdir=workdir,
            allowed_executables=["echo"],
        )

        print("--- agent response ---")
        print(result.text)
        print("\n--- proof this was real, not theater ---")
        print(f"Anthropic request_id: {result.request_id}")
        print(f"tokens: input={result.input_tokens} output={result.output_tokens}")
        print(f"sandboxed subprocess calls:\n{json.dumps(result.sandbox_calls, indent=2)}")

        assert result.sandbox_calls, "agent never called the sandboxed run_command tool"
        assert all(c["pid"] > 0 for c in result.sandbox_calls), "no real PID reported"


if __name__ == "__main__":
    asyncio.run(main())

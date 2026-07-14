"""Dev-only: regenerate the shared Features block in this repo's own
README.md and docs/PLAN.md from src/swarmkit/docs/generate.py's
FEATURE_SECTIONS. Run this after changing any capability description there,
instead of hand-editing the same prose into README.md and docs/PLAN.md
separately.
"""

from __future__ import annotations

from swarmkit.docs.generate import sync_repo_docs


def main() -> None:
    changed = sync_repo_docs(".")
    if not changed:
        print("README.md and docs/PLAN.md already match src/swarmkit/docs/generate.py")
        return
    for path in changed:
        print(f"updated {path}")


if __name__ == "__main__":
    main()

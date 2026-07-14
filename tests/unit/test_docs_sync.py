"""Drift guard: README.md and docs/PLAN.md embed a generated-features block
that must match src/swarmkit/docs/generate.py's feature_markdown() exactly.
If this fails, someone edited a capability description by hand instead of
running scripts/sync_docs.py — the whole point of sourcing it from one
place instead of hand-duplicating it in three files."""

from __future__ import annotations

from pathlib import Path

from swarmkit.docs.generate import FEATURES_MARKER_END, FEATURES_MARKER_START, feature_markdown

REPO_ROOT = Path(__file__).resolve().parents[2]


def _marked_block(text: str) -> str:
    start = text.index(FEATURES_MARKER_START) + len(FEATURES_MARKER_START)
    end = text.index(FEATURES_MARKER_END)
    return text[start:end].strip()


def test_readme_features_block_matches_generate_py():
    text = (REPO_ROOT / "README.md").read_text()
    assert _marked_block(text) == feature_markdown()


def test_plan_features_block_matches_generate_py():
    text = (REPO_ROOT / "docs" / "PLAN.md").read_text()
    assert _marked_block(text) == feature_markdown()


def test_sync_repo_docs_is_a_no_op_when_already_in_sync(tmp_path):
    (tmp_path / "docs").mkdir()
    readme = tmp_path / "README.md"
    plan = tmp_path / "docs" / "PLAN.md"
    stale = f"before\n{FEATURES_MARKER_START}\nstale content\n{FEATURES_MARKER_END}\nafter\n"
    readme.write_text(stale)
    plan.write_text(stale)

    from swarmkit.docs.generate import sync_repo_docs

    first_pass = sync_repo_docs(tmp_path)
    assert set(first_pass) == {readme, plan}

    second_pass = sync_repo_docs(tmp_path)
    assert second_pass == []


def test_sync_repo_docs_updates_a_stale_block(tmp_path):
    (tmp_path / "docs").mkdir()
    readme = tmp_path / "README.md"
    plan = tmp_path / "docs" / "PLAN.md"
    stale = f"before\n{FEATURES_MARKER_START}\nstale content\n{FEATURES_MARKER_END}\nafter\n"
    readme.write_text(stale)
    plan.write_text(stale)

    from swarmkit.docs.generate import sync_repo_docs

    changed = sync_repo_docs(tmp_path)
    assert set(changed) == {readme, plan}
    assert "stale content" not in readme.read_text()
    assert feature_markdown() in readme.read_text()

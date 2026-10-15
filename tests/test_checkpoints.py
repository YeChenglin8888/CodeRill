"""Tests for the file-change undo stack."""

from coderill import checkpoints
from coderill.tools import build_registry


def test_undo_restores_previous_bytes(tmp_path):
    checkpoints.clear()
    target = tmp_path / "a.txt"
    target.write_text("old")
    checkpoints.record(target)
    target.write_text("new")
    checkpoints.seal(target)

    checkpoints.undo()
    assert target.read_text() == "old"


def test_undo_removes_a_newly_created_file(tmp_path):
    checkpoints.clear()
    target = tmp_path / "b.txt"
    checkpoints.record(target)  # does not exist yet
    target.write_text("new")
    checkpoints.seal(target)

    checkpoints.undo()
    assert not target.exists()


def test_undo_with_empty_stack_reports_so():
    checkpoints.clear()
    assert "nothing" in checkpoints.undo()


def test_writers_take_checkpoints(tmp_path):
    checkpoints.clear()
    registry = build_registry(tmp_path)
    registry.dispatch("write_file", {"path": "f.txt", "content": "one"})
    registry.dispatch("write_file", {"path": "f.txt", "content": "two"})
    assert checkpoints.pending() == 2
    checkpoints.undo()
    assert (tmp_path / "f.txt").read_text() == "one"


def test_undo_refuses_to_overwrite_a_later_human_change(tmp_path):
    checkpoints.clear()
    registry = build_registry(tmp_path)
    registry.dispatch("write_file", {"path": "f.txt", "content": "agent"})
    (tmp_path / "f.txt").write_text("human")
    assert "refused" in checkpoints.undo()
    assert (tmp_path / "f.txt").read_text() == "human"

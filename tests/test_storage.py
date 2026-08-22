"""Tests for the CSV helpers, mainly the append-under-a-changed-header hazard."""

import storage

FIELDS = ["a", "b", "c"]


def test_write_then_read_round_trip(tmp_path):
    path = tmp_path / "out.csv"
    storage.write_csv([{"a": "1", "b": "2", "c": "3"}], path, FIELDS)
    assert storage.read_rows(path) == [{"a": "1", "b": "2", "c": "3"}]


def test_read_missing_file_is_empty(tmp_path):
    assert storage.read_rows(tmp_path / "nope.csv") == []


def test_append_writes_the_header_once(tmp_path):
    path = tmp_path / "out.csv"
    storage.append_csv([{"a": "1", "b": "2", "c": "3"}], path, FIELDS)
    storage.append_csv([{"a": "4", "b": "5", "c": "6"}], path, FIELDS)

    assert path.read_text(encoding="utf-8-sig").count("a,b,c") == 1
    assert [r["a"] for r in storage.read_rows(path)] == ["1", "4"]


def test_appending_after_a_column_is_added_rewrites_the_file(tmp_path):
    """Appending under a stale header would shift every value one column left."""
    path = tmp_path / "out.csv"
    storage.append_csv([{"a": "1", "b": "2", "c": "3"}], path, FIELDS)

    wider = ["a", "b", "extra", "c"]
    storage.append_csv([{"a": "4", "b": "5", "extra": "x", "c": "6"}], path, wider)

    rows = storage.read_rows(path)
    assert list(rows[0]) == wider, "header must be the new layout"
    assert rows[0] == {"a": "1", "b": "2", "extra": "", "c": "3"}, "old row keeps its values"
    assert rows[1]["extra"] == "x"
    assert rows[1]["c"] == "6", "values must not shift into the wrong column"


def test_reordered_columns_also_trigger_a_rewrite(tmp_path):
    path = tmp_path / "out.csv"
    storage.append_csv([{"a": "1", "b": "2", "c": "3"}], path, FIELDS)
    storage.append_csv([{"a": "4", "b": "5", "c": "6"}], path, ["c", "b", "a"])

    rows = storage.read_rows(path)
    assert list(rows[0]) == ["c", "b", "a"]
    assert rows[0] == {"c": "3", "b": "2", "a": "1"}


def test_parent_directory_is_created(tmp_path):
    path = tmp_path / "nested" / "deeper" / "out.csv"
    storage.write_csv([{"a": "1", "b": "2", "c": "3"}], path, FIELDS)
    assert path.exists()


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "out.csv"
    storage.write_csv([{"a": "1", "b": "2", "c": "3", "surplus": "x"}], path, FIELDS)
    assert storage.read_rows(path) == [{"a": "1", "b": "2", "c": "3"}]

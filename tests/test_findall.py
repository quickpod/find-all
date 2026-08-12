"""Deterministic, headless tests for the findall package.

Every test drives a fresh, isolated FindAll home directory (``FINDALL_HOME``
pointed at a tmp dir) so nothing touches the real user config, and builds a
small tree with known words plus a binary file.
"""

from __future__ import annotations

import os

import pytest

from findall import (
    FindAllError,
    build_index,
    extract_text,
    get_source,
    list_sources,
    remove_source,
    search,
    update_index,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Point FindAll's data dir at an isolated tmp folder."""
    d = tmp_path / "findall_home"
    d.mkdir()
    monkeypatch.setenv("FINDALL_HOME", str(d))
    return d


@pytest.fixture()
def tree(tmp_path):
    """A small corpus: three text files with known words + one binary file."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "alpha.txt").write_text(
        "The quick brown fox jumps over the lazy dog.\n", encoding="utf-8")
    (root / "notes.md").write_text(
        "# Banana bread\nA recipe featuring banana and walnuts.\n",
        encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "script.py").write_text(
        "def greet():\n    return 'hello python world'\n", encoding="utf-8")
    # A binary blob (has NUL bytes) -> must index by name only, never crash.
    (root / "image.bin").write_bytes(bytes(range(256)) * 8)
    return root


# --------------------------------------------------------------------------- #
# content extraction
# --------------------------------------------------------------------------- #
def test_extract_text_text_vs_binary(tree):
    assert "quick brown fox" in extract_text(str(tree / "alpha.txt"))
    # binary -> empty content
    assert extract_text(str(tree / "image.bin")) == ""


# --------------------------------------------------------------------------- #
# build + search
# --------------------------------------------------------------------------- #
def test_build_and_search_by_filename(home, tree):
    res = build_index(str(tree), name="corpus")
    assert res["count"] == 4  # 3 text files + 1 binary, all indexed (by name)

    hits = search("corpus", "alpha")
    paths = [h["path"] for h in hits]
    assert any(p.endswith("alpha.txt") for p in paths)


def test_search_by_content_word_has_snippet(home, tree):
    build_index(str(tree), name="corpus")
    hits = search("corpus", "banana")
    assert hits, "expected a content match for 'banana'"
    top = hits[0]
    assert top["path"].endswith("notes.md")
    assert "banana" in top["snippet"].lower()


def test_fuzzy_typo_still_matches(home, tree):
    build_index(str(tree), name="corpus")
    # 'pyhton' is a transposition typo of 'python' (in script.py content).
    hits = search("corpus", "pyhton", fuzzy=True)
    assert any(h["path"].endswith("script.py") for h in hits)
    # With fuzzy disabled the typo should NOT match.
    exact = search("corpus", "pyhton", fuzzy=False)
    assert not any(h["path"].endswith("script.py") for h in exact)


def test_filter_by_ext(home, tree):
    build_index(str(tree), name="corpus")
    # 'the' appears in alpha.txt; restrict to markdown only -> no alpha.txt.
    hits = search("corpus", "banana", filters={"ext": "md"})
    assert hits and all(h["ext"] == "md" for h in hits)
    none = search("corpus", "banana", filters={"ext": "txt"})
    assert none == []


def test_filter_by_size(home, tree):
    build_index(str(tree), name="corpus")
    # The binary file is the largest; match everything by name and bound size.
    small = search("corpus", "alpha OR notes OR script OR image",
                   filters={"max_size": 200})
    assert all(h["size"] <= 200 for h in small)
    assert not any(h["path"].endswith("image.bin") for h in small)


def test_binary_indexed_by_name_only(home, tree):
    build_index(str(tree), name="corpus")
    # found by filename...
    by_name = search("corpus", "image")
    assert any(h["path"].endswith("image.bin") for h in by_name)
    # ...but its content is empty, so a content-only term yields no snippet text.
    hit = next(h for h in by_name if h["path"].endswith("image.bin"))
    assert hit["snippet"] == ""


# --------------------------------------------------------------------------- #
# incremental update
# --------------------------------------------------------------------------- #
def test_update_index_adds_and_drops(home, tree):
    build_index(str(tree), name="corpus")
    assert not search("corpus", "cherry")

    # add a new file...
    (tree / "fruit.txt").write_text("cherry and mango\n", encoding="utf-8")
    # ...and delete an existing one.
    os.remove(tree / "alpha.txt")

    stats = update_index("corpus")
    assert stats["added"] == 1
    assert stats["deleted"] == 1

    assert any(h["path"].endswith("fruit.txt")
               for h in search("corpus", "cherry"))
    assert not any(h["path"].endswith("alpha.txt")
                   for h in search("corpus", "quick"))


# --------------------------------------------------------------------------- #
# sources manifest + errors
# --------------------------------------------------------------------------- #
def test_sources_registry_and_remove(home, tree):
    build_index(str(tree), name="corpus")
    names = [n for n, _ in list_sources()]
    assert "corpus" in names
    assert get_source("corpus")["root"] == str(tree)

    remove_source("corpus")
    assert "corpus" not in [n for n, _ in list_sources()]
    with pytest.raises(FindAllError):
        search("corpus", "anything")


def test_search_missing_source_raises(home):
    with pytest.raises(FindAllError):
        search("nope", "query")


def test_build_index_bad_root_raises(home, tmp_path):
    with pytest.raises(FindAllError):
        build_index(str(tmp_path / "does-not-exist"), name="x")


def test_ext_restricted_build(home, tree):
    # Only index .txt files -> markdown/py/binary excluded.
    res = build_index(str(tree), name="txtonly", exts=["txt"])
    assert res["count"] == 1
    assert not search("txtonly", "banana")  # notes.md was excluded
    assert search("txtonly", "quick")       # alpha.txt present

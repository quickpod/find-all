"""GUI tests for the 1.1.0 Aura layout-language rework (Everything benchmark).

Pure helpers run anywhere; the App tests need a display (run under
``xvfb-run``) and are skipped headless.  FINDALL_HOME keeps config + indexes
hermetic in the test tmp dir.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from findall import gui, guiconfig  # noqa: E402
from findall import index as index_mod  # noqa: E402


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
def test_file_kind():
    assert gui.file_kind("notes.md") == "docs"
    assert gui.file_kind("photo.JPG") == "images"
    assert gui.file_kind("song.flac") == "audio"
    assert gui.file_kind("clip.mkv") == "video"
    assert gui.file_kind("binary.exe") == "other"
    assert gui.file_kind("") == "other"


def test_passes_chip():
    assert gui.passes_chip("a.py", "All")
    assert gui.passes_chip("a.md", "Documents")
    assert not gui.passes_chip("a.md", "Images")
    assert gui.passes_chip("a.exe", "Other")
    assert not gui.passes_chip("a.png", "Other")


def test_theme_defaults_to_system(tmp_path, monkeypatch):
    monkeypatch.setenv("FINDALL_HOME", str(tmp_path))
    assert guiconfig.get_theme() == "system"
    guiconfig.set_theme("dark")
    assert guiconfig.get_theme() == "dark"
    guiconfig.set_theme("system")
    assert guiconfig.get_theme() == "system"


# ---------------------------------------------------------------------------
# the App under Xvfb
# ---------------------------------------------------------------------------
def _display():
    return bool(os.environ.get("DISPLAY")) and os.name != "nt"


needs_display = pytest.mark.skipif(not _display(),
                                   reason="needs a display (xvfb-run)")


@pytest.fixture()
def corpus(tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "alpha-notes.md").write_text("aura design tokens", encoding="utf-8")
    (d / "beta.txt").write_text("plain aura text", encoding="utf-8")
    (d / "gamma.png").write_bytes(b"\x89PNG\r\n\x1a\n000")
    return str(d)


@pytest.fixture()
def app(tmp_path, monkeypatch, corpus):
    monkeypatch.setenv("FINDALL_HOME", str(tmp_path / "home"))
    guiconfig.set_theme("dark")     # deterministic; no OS follow in tests
    index_mod.build_index(corpus, name="corpus", include_content=True)
    App = gui.build_app()
    a = App()
    a.update()
    yield a
    try:
        a.destroy()
    except Exception:
        pass


def _wait_results(a, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        a.update()
        if a.tree.get_children():
            return True
        time.sleep(0.05)
    return False


@needs_display
def test_sidebar_source_library(app):
    assert "corpus" in app._src_rows
    assert app._active_source == "corpus"


@needs_display
def test_search_and_type_filter(app):
    app.search_entry.set("aura")
    app._trigger_search()
    assert _wait_results(app)
    assert len(app._rows) == 2                 # both text files match
    app._set_chip("Images")
    app.update()
    assert len(app._rows) == 0
    app._set_chip("Documents")
    app.update()
    assert len(app._rows) == 2
    app._set_chip("All")
    app.update()


@needs_display
def test_sort_by_name(app):
    app.search_entry.set("aura")
    app._trigger_search()
    assert _wait_results(app)
    app._sort_by("name")
    names = [app.tree.item(i)["values"][0] for i in app.tree.get_children()]
    assert names == sorted(names, key=str.lower)
    app._sort_by("name")                       # toggles to descending
    names2 = [app.tree.item(i)["values"][0] for i in app.tree.get_children()]
    assert names2 == list(reversed(names))


@needs_display
def test_no_match_empty_state(app):
    app.search_entry.set("zzz-not-there")
    app._trigger_search()
    end = time.time() + 6
    while time.time() < end and app.empty_results.winfo_manager() != "place":
        app.update()
        time.sleep(0.05)
    assert app.empty_results.winfo_manager() == "place"


@needs_display
def test_theme_flip_smoke(app):
    app.set_theme("light")
    app.update()
    app.set_theme("dark")
    app.update()
    assert app.theme == "dark"

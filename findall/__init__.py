"""findall -- instant offline full-text + filename search built on Whoosh.

Public API::

    from findall import build_index, update_index, search
    build_index("/some/folder", name="docs")
    for hit in search("docs", "quarterly report"):
        print(hit["path"], hit["snippet"])

Every function raises :class:`FindAllError` (and only that) on failure, so the
CLI and the tkinter GUI have a single exception to catch.  100% AI-built, open
source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

from .content import extract_text, is_texty
from .errors import FindAllError
from .index import (
    build_index,
    get_source,
    list_sources,
    load_sources,
    open_index,
    remove_source,
    update_index,
)
from .search import search

__version__ = "1.1.0"

__all__ = [
    "FindAllError",
    "build_index",
    "update_index",
    "open_index",
    "search",
    "list_sources",
    "load_sources",
    "get_source",
    "remove_source",
    "extract_text",
    "is_texty",
    "__version__",
]

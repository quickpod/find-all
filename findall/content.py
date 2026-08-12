"""Safe plain-text extraction for indexing.

The rule is simple and defensive: text-like files are read (encoding-guarded,
size-capped) so their *content* can be indexed; everything else -- images,
archives, executables, office blobs -- returns ``""`` so the file is still
indexed by *name only* and never crashes the walk.

A file is treated as text if its extension is in :data:`TEXT_EXTS`, or (for
unknown extensions) if a quick sniff of its first bytes contains no NUL byte.
A NUL byte anywhere in the read window always forces a binary verdict, so a
mislabelled ``.txt`` that is really binary is handled gracefully too.
"""

from __future__ import annotations

import os
import re

# Extensions we always try to read as text.
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log", ".json",
    ".py", ".pyw", ".html", ".htm", ".xml", ".yaml", ".yml", ".ini", ".cfg",
    ".conf", ".toml", ".js", ".ts", ".css", ".c", ".h", ".cpp", ".hpp",
    ".java", ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".ps1", ".sql",
    ".tex", ".srt", ".vtt", ".env", ".gitignore", ".properties",
}

# Read at most this many bytes for content (keeps huge logs from blowing memory).
MAX_BYTES = 2_000_000
# Bytes sampled to decide whether an unknown-extension file is text.
SNIFF_BYTES = 4096

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def ext_of(path):
    """Lowercase extension of *path* WITHOUT the leading dot (``"txt"``, ``""``)."""
    return os.path.splitext(path)[1].lower().lstrip(".")


def is_texty(path):
    """Best-effort check: would :func:`extract_text` read content for *path*?"""
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTS:
        return True
    return _looks_text(path)


def _looks_text(path):
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(SNIFF_BYTES)
    except Exception:
        return False
    if not chunk:
        return False  # empty file: nothing to index as content
    return b"\x00" not in chunk


def extract_text(path):
    """Return decoded text for *path*, or ``""`` when it is not safe/plain text.

    Never raises: any error (permissions, disappearance, odd encoding) collapses
    to ``""`` so the caller keeps indexing the file by name.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext not in TEXT_EXTS and not _looks_text(path):
            return ""
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES)
    except Exception:
        return ""
    if b"\x00" in raw:  # binary despite a text-ish extension
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", "replace")
    if ext in (".html", ".htm", ".xml"):
        text = _strip_markup(text)
    return text


def _strip_markup(text):
    """Crude tag stripper so HTML/XML snippets show words, not angle brackets."""
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text

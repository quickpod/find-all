"""Whoosh index build / open / incremental-update, keyed by named source folder.

Each *source* is a user-named root folder.  Its index lives in its own directory
under ``<config_dir>/indexes/`` and a small JSON manifest
(``<config_dir>/sources.json``) records the root and the options it was built
with so :func:`update_index` and the search layer can find it again.

Stored per file: absolute ``path`` (the unique key), ``filename``, ``ext``,
``size`` and ``mtime`` -- plus extracted ``content`` for text-like files (empty
string for binaries, which are therefore searchable by name only).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil

from whoosh import index as _windex
from whoosh.analysis import LowercaseFilter, RegexTokenizer, StandardAnalyzer
from whoosh.fields import ID, NUMERIC, TEXT, Schema

from . import guiconfig
from .content import ext_of, extract_text
from .errors import FindAllError

# Keep every token (including short words and stop-words like "the") so content
# search and snippet highlighting are predictable.
_ANALYZER = StandardAnalyzer(stoplist=None, minsize=1)
# Filenames tokenise on runs of alphanumerics so "image.bin" and "report_2024"
# split into "image"/"bin" and "report"/"2024" -- the default tokenizer would
# keep dotted names whole and miss a search for just "image".
_NAME_ANALYZER = RegexTokenizer(r"[A-Za-z0-9]+") | LowercaseFilter()

SCHEMA = Schema(
    path=ID(unique=True, stored=True),
    filename=TEXT(stored=True, analyzer=_NAME_ANALYZER),
    ext=ID(stored=True),
    # signed 64-bit: an unsigned NUMERIC breaks query-side NumericRange filtering.
    size=NUMERIC(stored=True, sortable=True, bits=64),
    # NOTE: no sortable= on the float field -- Whoosh's sortable column factory
    # mis-packs float NUMERICs ("required argument is not an integer").  Range
    # filtering on mtime works without it.
    mtime=NUMERIC(stored=True, numtype=float),
    content=TEXT(stored=True, analyzer=_ANALYZER),
)


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #
def _base_dir():
    return guiconfig.config_dir()


def indexes_dir():
    return os.path.join(_base_dir(), "indexes")


def manifest_path():
    return os.path.join(_base_dir(), "sources.json")


def _index_dir(name):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("_") or "src"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return os.path.join(indexes_dir(), f"{safe}-{digest}")


# --------------------------------------------------------------------------- #
# Source manifest (defensive JSON)
# --------------------------------------------------------------------------- #
def load_sources():
    """Return the manifest dict ``{name: {...}}`` (never raises)."""
    try:
        with open(manifest_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _save_sources(sources):
    try:
        os.makedirs(_base_dir(), exist_ok=True)
        tmp = manifest_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sources, fh, indent=2)
        os.replace(tmp, manifest_path())
    except Exception:
        pass


def get_source(name):
    """Return the manifest entry for *name*, or ``None``."""
    return load_sources().get(name)


def list_sources():
    """Return a sorted list of ``(name, info)`` pairs."""
    return sorted(load_sources().items())


def _record_source(name, info):
    sources = load_sources()
    sources[name] = info
    _save_sources(sources)


def remove_source(name):
    """Forget *name* and delete its on-disk index. Raise if it is unknown."""
    sources = load_sources()
    if name not in sources:
        raise FindAllError(f"no such source: {name!r}")
    del sources[name]
    _save_sources(sources)
    shutil.rmtree(_index_dir(name), ignore_errors=True)


# --------------------------------------------------------------------------- #
# Extension filtering + walking
# --------------------------------------------------------------------------- #
def _norm_exts(exts):
    """Normalise *exts* (str/iterable) to a lowercase ``{"txt", ...}`` set or None."""
    if not exts:
        return None
    if isinstance(exts, str):
        exts = [exts]
    out = set()
    for e in exts:
        e = str(e).strip().lower().lstrip(".")
        if e:
            out.add(e)
    return out or None


def _walk(root, exts):
    """Yield absolute file paths under *root*, filtered by extension set *exts*.

    Deterministic: directories and files are visited in sorted order.
    """
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            if not os.path.isfile(fp):
                continue
            if exts is not None and ext_of(fp) not in exts:
                continue
            files.append(fp)
    return files


def _add(writer, fp, include_content):
    try:
        st = os.stat(fp)
    except OSError:
        return False
    content = extract_text(fp) if include_content else ""
    writer.update_document(
        path=fp,
        filename=os.path.basename(fp),
        ext=ext_of(fp),
        size=int(st.st_size),
        mtime=float(st.st_mtime),
        content=content,
    )
    return True


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def build_index(root, name, include_content=True, exts=None, progress=None):
    """Build (or rebuild from scratch) the index for source *name* rooted at *root*.

    ``include_content`` toggles text extraction; ``exts`` (str or iterable)
    restricts which file types are indexed at all.  ``progress`` is an optional
    ``callback(done, total, path)`` invoked per file.  Returns a summary dict.
    """
    if not name or not str(name).strip():
        raise FindAllError("a source name is required")
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise FindAllError(f"not a folder: {root}")

    exts_norm = _norm_exts(exts)
    idir = _index_dir(name)
    shutil.rmtree(idir, ignore_errors=True)
    os.makedirs(idir, exist_ok=True)

    try:
        ix = _windex.create_in(idir, SCHEMA)
    except Exception as exc:  # pragma: no cover - filesystem edge cases
        raise FindAllError(f"could not create index for {name!r}: {exc}") from exc

    files = _walk(root, exts_norm)
    total = len(files)
    count = 0
    writer = ix.writer()
    try:
        for i, fp in enumerate(files, 1):
            if _add(writer, fp, include_content):
                count += 1
            if progress:
                try:
                    progress(i, total, fp)
                except Exception:
                    pass
        writer.commit()
    except Exception as exc:
        writer.cancel()
        raise FindAllError(f"indexing failed for {name!r}: {exc}") from exc
    finally:
        ix.close()

    _record_source(name, {
        "root": root,
        "include_content": bool(include_content),
        "exts": sorted(exts_norm) if exts_norm else None,
        "count": count,
        "indexed_at": _now(),
    })
    return {"name": name, "root": root, "count": count}


def open_index(name):
    """Open the existing index for *name*. Raise :class:`FindAllError` if absent."""
    idir = _index_dir(name)
    if not os.path.isdir(idir) or not _windex.exists_in(idir):
        raise FindAllError(
            f"no index for {name!r} -- build one first (findall index <root> --name {name})"
        )
    try:
        return _windex.open_dir(idir)
    except Exception as exc:  # pragma: no cover
        raise FindAllError(f"could not open index for {name!r}: {exc}") from exc


def update_index(name, progress=None):
    """Incrementally reconcile the index for *name* with what is on disk.

    Adds new files, re-indexes files whose ``mtime``/``size`` changed, and drops
    files that disappeared. Returns ``{"added","updated","deleted","count"}``.
    """
    src = get_source(name)
    if src is None:
        raise FindAllError(f"no such source: {name!r} -- build it first")
    root = src.get("root")
    if not root or not os.path.isdir(root):
        raise FindAllError(f"source root is missing: {root}")
    include_content = bool(src.get("include_content", True))
    exts_norm = _norm_exts(src.get("exts"))

    ix = open_index(name)
    added = updated = deleted = 0
    try:
        existing = {}
        with ix.searcher() as searcher:
            for fields in searcher.all_stored_fields():
                existing[fields["path"]] = (fields.get("mtime"), fields.get("size"))

        disk = _walk(root, exts_norm)
        disk_set = set(disk)
        total = len(disk)
        writer = ix.writer()
        try:
            for i, fp in enumerate(disk, 1):
                prev = existing.get(fp)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                if prev is None:
                    if _add(writer, fp, include_content):
                        added += 1
                else:
                    pm, ps = prev
                    changed = (ps != int(st.st_size)
                               or pm is None
                               or abs(float(pm) - float(st.st_mtime)) > 1e-6)
                    if changed and _add(writer, fp, include_content):
                        updated += 1
                if progress:
                    try:
                        progress(i, total, fp)
                    except Exception:
                        pass
            for path in existing:
                if path not in disk_set:
                    writer.delete_by_term("path", path)
                    deleted += 1
            writer.commit()
        except Exception as exc:
            writer.cancel()
            raise FindAllError(f"update failed for {name!r}: {exc}") from exc

        with ix.searcher() as searcher:
            count = searcher.doc_count()
    finally:
        ix.close()

    src.update({"count": count, "indexed_at": _now()})
    _record_source(name, src)
    return {"added": added, "updated": updated, "deleted": deleted, "count": count}


def _now():
    import time
    return time.time()

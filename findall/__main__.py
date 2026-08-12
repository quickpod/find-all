"""Command-line interface: ``python -m findall <command> ...``.

Commands::

    findall index <root> --name N [--no-content] [--ext txt md ...]
    findall update <name>
    findall search <name> "query" [--limit N] [--ext ...] [--min-size B] \
                                  [--max-size B] [--no-fuzzy]
    findall sources [--remove NAME]

Exits 0 on success and 1 (with a clean ``error: ...`` line, never a traceback)
on any :class:`FindAllError`.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys

from . import (
    FindAllError,
    build_index,
    list_sources,
    remove_source,
    search,
    update_index,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _human_size(num):
    size = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


def _fmt_time(ts):
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


# --------------------------------------------------------------------------- #
# command handlers
# --------------------------------------------------------------------------- #
def cmd_index(a):
    res = build_index(a.root, name=a.name, include_content=not a.no_content,
                      exts=a.ext)
    kind = "name+content" if not a.no_content else "name only"
    print(f"Indexed {res['count']} file(s) from {res['root']} "
          f"as {res['name']!r} ({kind}).")


def cmd_update(a):
    res = update_index(a.name)
    print(f"Updated {a.name!r}: +{res['added']} added, "
          f"~{res['updated']} changed, -{res['deleted']} removed "
          f"({res['count']} indexed).")


def cmd_search(a):
    filters = {}
    if a.ext:
        filters["ext"] = a.ext
    if a.min_size is not None:
        filters["min_size"] = a.min_size
    if a.max_size is not None:
        filters["max_size"] = a.max_size
    results = search(a.name, a.query, limit=a.limit, fuzzy=not a.no_fuzzy,
                     filters=filters or None)
    if not results:
        print("(no matches)")
        return
    for r in results:
        print(f"{r['path']}")
        meta = f"    {_human_size(r['size'])}  {_fmt_time(r['mtime'])}  " \
               f"score={r['score']:.2f}"
        print(meta)
        if r["snippet"]:
            snip = " ".join(r["snippet"].split())
            print(f"    … {snip}")
    print(f"\n{len(results)} match(es).")


def cmd_sources(a):
    if a.remove:
        remove_source(a.remove)
        print(f"Removed source {a.remove!r}.")
        return
    sources = list_sources()
    if not sources:
        print("(no sources yet -- add one with: findall index <root> --name N)")
        return
    for name, info in sources:
        exts = ",".join(info.get("exts")) if info.get("exts") else "all"
        print(f"{name}\n    root: {info.get('root')}\n    "
              f"files: {info.get('count', '?')}  exts: {exts}  "
              f"content: {'yes' if info.get('include_content', True) else 'no'}  "
              f"updated: {_fmt_time(info.get('indexed_at'))}")


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="findall",
        description="Instant offline filename + full-text search (Whoosh).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, help, handler):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(func=handler)
        return sp

    s = add("index", "Build/rebuild an index for a folder", cmd_index)
    s.add_argument("root", help="folder to index")
    s.add_argument("--name", required=True, help="name this source")
    s.add_argument("--no-content", action="store_true",
                   help="index filenames only (skip text extraction)")
    s.add_argument("--ext", nargs="+",
                   help="only index these extensions, e.g. --ext txt md py")

    s = add("update", "Incrementally re-index a source", cmd_update)
    s.add_argument("name")

    s = add("search", "Search a source by name and/or content", cmd_search)
    s.add_argument("name")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--ext", nargs="+", help="filter by extension(s)")
    s.add_argument("--min-size", type=int, help="minimum file size in bytes")
    s.add_argument("--max-size", type=int, help="maximum file size in bytes")
    s.add_argument("--no-fuzzy", action="store_true",
                   help="require exact term matches (disable typo tolerance)")

    s = add("sources", "List sources (or remove one)", cmd_sources)
    s.add_argument("--remove", metavar="NAME", help="delete a source and its index")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except FindAllError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

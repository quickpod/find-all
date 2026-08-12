"""Query a named index: filename + content, fuzzy matching, and filters.

:func:`search` returns a list of plain dicts (``path``, ``name``, ``size``,
``mtime``, ``score``, ``snippet``) sorted best-first.  A single query is matched
against both the filename and the extracted content, so callers get an
"Everything-style" launcher that also looks inside documents.
"""

from __future__ import annotations

from whoosh import highlight
from whoosh.qparser import FuzzyTermPlugin, MultifieldParser, OrGroup
from whoosh.query import And, NumericRange, Or, Term

from .errors import FindAllError
from .index import open_index

# Characters that mean "the user is writing their own query operators" -- we
# leave those tokens untouched rather than bolting a fuzzy marker onto them.
_SPECIAL = set('~*?"()[]:^')


class _PlainFormatter(highlight.Formatter):
    """Highlight formatter that returns matched text unchanged (no markup)."""

    def format_token(self, text, token, replace=False):
        return highlight.get_text(text, token, replace)


def _fuzzify(query, fuzzy):
    """Append Whoosh fuzzy markers (``~n``) to plain word tokens when *fuzzy*."""
    if not fuzzy:
        return query
    parts = []
    for tok in query.split():
        if not tok or any(c in _SPECIAL for c in tok):
            parts.append(tok)
            continue
        length = len(tok)
        if length >= 6:
            parts.append(tok + "~2")
        elif length >= 4:
            parts.append(tok + "~1")
        else:
            parts.append(tok)
    return " ".join(parts)


def _norm_ext(e):
    return str(e).strip().lower().lstrip(".")


def _build_filter(filters):
    """Turn a filters dict into a Whoosh filter query (or None).

    Recognised keys: ``ext`` (str or iterable), ``min_size``/``max_size`` (bytes),
    ``date_from``/``date_to`` (epoch seconds, matched against mtime).
    """
    if not filters:
        return None
    clauses = []

    ext = filters.get("ext")
    if ext:
        exts = [ext] if isinstance(ext, str) else list(ext)
        terms = [Term("ext", _norm_ext(e)) for e in exts if _norm_ext(e)]
        if terms:
            clauses.append(terms[0] if len(terms) == 1 else Or(terms))

    mn, mx = filters.get("min_size"), filters.get("max_size")
    if mn is not None or mx is not None:
        clauses.append(NumericRange("size", mn, mx))

    df, dt = filters.get("date_from"), filters.get("date_to")
    if df is not None or dt is not None:
        clauses.append(NumericRange("mtime", df, dt))

    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else And(clauses)


def search(name, query, limit=200, fuzzy=True, filters=None):
    """Search source *name* for *query*; return a best-first list of result dicts.

    Matches filenames and content together. ``fuzzy`` tolerates small typos;
    ``filters`` narrows by extension / size / date. Raises :class:`FindAllError`
    if the source has no index or the query cannot be parsed.
    """
    query = (query or "").strip()
    ix = open_index(name)
    if not query:
        return []

    parser = MultifieldParser(["filename", "content"], schema=ix.schema,
                              group=OrGroup)
    parser.add_plugin(FuzzyTermPlugin())
    try:
        parsed = parser.parse(_fuzzify(query, fuzzy))
    except Exception as exc:
        ix.close()
        raise FindAllError(f"could not parse query {query!r}: {exc}") from exc

    filt = _build_filter(filters)
    results = []
    try:
        with ix.searcher() as searcher:
            hits = searcher.search(parsed, limit=limit, filter=filt)
            hits.formatter = _PlainFormatter()
            hits.fragmenter = highlight.ContextFragmenter(maxchars=180, surround=50)
            for hit in hits:
                content = hit.get("content") or ""
                snippet = ""
                if content:
                    try:
                        snippet = hit.highlights("content", text=content, top=1)
                    except Exception:
                        snippet = ""
                    if not snippet:
                        snippet = " ".join(content.split())[:160]
                results.append({
                    "path": hit.get("path"),
                    "name": hit.get("filename"),
                    "ext": hit.get("ext"),
                    "size": hit.get("size"),
                    "mtime": hit.get("mtime"),
                    "score": float(hit.score) if hit.score is not None else 0.0,
                    "snippet": snippet,
                })
    finally:
        ix.close()
    return results

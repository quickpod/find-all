# FindAll

A fast, **offline**, **100% open-source** instant file & content search for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/find-all).

> **100% AI-built and open source.** Apache-2.0.

## What it does

Index chosen folders once, then search filenames and file contents instantly with fuzzy matching, filters (type/size/date) and live previews. Incremental re-indexing keeps it current. An 'Everything'-style launcher that also searches inside documents — fully local.

## Install

Download **`FindAll-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/find-all) or the [GitHub release](https://github.com/quickpod/find-all/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python find_all_app.py          # GUI
python -m findall --help    # CLI
```


## Features

- **Filename + full-text search in one query.** Type once; FindAll matches file
  names *and* the text inside documents (`.txt`, `.md`, `.csv`, `.log`, `.json`,
  `.py`, `.html`, and other plain-text types). Binary files are indexed by name
  only, never scanned.
- **Instant, as-you-type results** in the GUI — debounced and run on a
  background thread so the window stays responsive.
- **Fuzzy matching** tolerates typos (`bananna` still finds *banana*); disable
  it for exact matches.
- **Filters** by extension, file size and modification date.
- **Content snippets** show the matching text in context, with one-click
  *Open file* / *Open folder*.
- **Named sources.** Index any number of folders, each under its own name;
  re-index incrementally — new and changed files are picked up and deleted files
  are dropped.
- **Fully offline & local.** Built on [Whoosh](https://whoosh.readthedocs.io)
  (BSD). Indexes live under `%LOCALAPPDATA%\FindAll` (or `~/.findall`). Nothing
  is ever uploaded.

## CLI examples

```sh
# Index a folder under a chosen name (add --no-content for filenames only)
python -m findall index "C:\Users\me\Documents" --name docs

# Restrict to certain file types
python -m findall index ./project --name code --ext py md txt

# Search names + contents (fuzzy by default)
python -m findall search docs "quarterly report"

# Filters: extension, size, exact matching
python -m findall search docs invoice --ext pdf --min-size 1000 --no-fuzzy

# Incrementally re-index (adds new/changed files, drops deleted)
python -m findall update docs

# List sources, or remove one (deletes its index)
python -m findall sources
python -m findall sources --remove docs
```

## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.

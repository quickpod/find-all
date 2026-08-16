r"""Tiny JSON-backed config + data locations for FindAll.

This module owns two things and never raises:

* the GUI's small preferences (chosen theme and a short list of recent root
  folders), persisted to ``config.json``;
* the *base directory* every other module builds on -- the Whoosh indexes and
  the source manifest live under it.

On Windows the base dir is ``%LOCALAPPDATA%\FindAll``; elsewhere it falls back
to ``~/.findall``.  Setting the ``FINDALL_HOME`` environment variable overrides
both (used by the test-suite to keep everything inside a tmp tree).  Every
function is defensive -- a corrupt or unreadable config must never stop the app
from starting.
"""

from __future__ import annotations

import json
import os

APP_DIRNAME = "FindAll"
CONFIG_NAME = "config.json"
MAX_RECENT = 10
VALID_THEMES = ("system", "light", "dark")


def config_dir():
    r"""Base directory for all FindAll data (created on demand by callers).

    Order of precedence: ``$FINDALL_HOME`` -> ``%LOCALAPPDATA%\FindAll`` on
    Windows -> ``~/.findall`` everywhere else.
    """
    override = os.environ.get("FINDALL_HOME")
    if override:
        return override
    local = os.environ.get("LOCALAPPDATA")
    if local and os.name == "nt":
        return os.path.join(local, APP_DIRNAME)
    return os.path.join(os.path.expanduser("~"), "." + APP_DIRNAME.lower())


def config_path():
    return os.path.join(config_dir(), CONFIG_NAME)


def _defaults():
    return {"theme": "system", "recent": []}


def load():
    """Return the config dict, always with ``theme`` and ``recent`` keys."""
    cfg = _defaults()
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            theme = data.get("theme")
            if theme in VALID_THEMES:
                cfg["theme"] = theme
            recent = data.get("recent")
            if isinstance(recent, list):
                cfg["recent"] = [p for p in recent if isinstance(p, str)][:MAX_RECENT]
    except Exception:
        pass  # missing/corrupt -> defaults; never fatal
    return cfg


def save(cfg):
    """Persist *cfg* (best-effort; failures are swallowed)."""
    try:
        os.makedirs(config_dir(), exist_ok=True)
        clean = {
            "theme": cfg.get("theme") if cfg.get("theme") in VALID_THEMES else "system",
            "recent": [p for p in cfg.get("recent", []) if isinstance(p, str)][:MAX_RECENT],
        }
        tmp = config_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
        os.replace(tmp, config_path())
    except Exception:
        pass


def get_theme():
    return load().get("theme", "system")


def set_theme(theme):
    if theme not in VALID_THEMES:
        return
    cfg = load()
    cfg["theme"] = theme
    save(cfg)


def get_recent():
    return load().get("recent", [])


def add_recent(path):
    """Push *path* to the front of the recent-roots list (most-recent-first).

    De-duplicates by absolute path and keeps at most ``MAX_RECENT`` entries.
    """
    if not path:
        return
    ap = _abs(path)
    cfg = load()
    recent = [p for p in cfg.get("recent", []) if _abs(p) != ap]
    recent.insert(0, ap)
    cfg["recent"] = recent[:MAX_RECENT]
    save(cfg)


def clear_recent():
    cfg = load()
    cfg["recent"] = []
    save(cfg)


def _abs(p):
    try:
        return os.path.abspath(p)
    except Exception:
        return p

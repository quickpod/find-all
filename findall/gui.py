#!/usr/bin/env python3
r"""FindAll -- a pure-stdlib tkinter GUI on top of the ``findall`` API.

A single window, search-first: a search box at the top (as-you-type, debounced,
threaded) with a source-folder selector; a results list showing name / size /
modified plus a content snippet, with "Open file" / "Open folder"; and a
"Manage sources" dialog to add a folder and (re)index it with a progress bar.

Design goals baked in here (mirrors the QuickOpen house style):
  * pure standard-library tkinter/ttk -- NO third-party GUI deps.  Dark mode is
    a ttk-style + palette swap.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a message, returns 0) with no display.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.
  * Indexing and search run on background threads; results are marshalled back
    with ``self.after`` and errors are shown in an inline bar, never as a
    traceback.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# tkinter is imported lazily inside main()/build_app so merely importing this
# module (e.g. during packaging or on a headless CI box) never fails.

APP_NAME = "FindAll"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "FindAll — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"

# ---- colour palettes (mirror the QuickOpen palette) -------------------------
PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e",
    },
}


# ---------------------------------------------------------------------------
# Asset / frozen handling  +  small OS helpers
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build.

    For a frozen exe we look only at ``sys._MEIPASS`` and the executable's own
    directory (never ``__file__``).  From source we also consult the package
    dir, the repo root and the CWD.  Returns an absolute path or ``None``.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def human_size(num_bytes):
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


def fmt_time(ts):
    import datetime
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def open_in_file_manager(path):
    """Best-effort 'reveal in file manager', guarded on every platform."""
    try:
        folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
        if hasattr(os, "startfile"):
            os.startfile(folder)  # noqa: S606 - intended
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False


def open_with_default_app(path):
    """Open a file with the OS default application, guarded."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import.

    Kept inside a function so this module imports cleanly without a display.
    """
    import tkinter as tk
    from tkinter import filedialog, ttk

    from . import guiconfig, index as index_mod, search as search_mod
    from .errors import FindAllError

    FONT = "Segoe UI"

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("1040x660")
            self.minsize(820, 520)

            self.theme = guiconfig.get_theme()
            self._busy = False
            self._tracked = []          # (tk_widget, role) for manual re-theming
            self._img_refs = []
            self._results = []          # current result dicts, row-indexed
            self._search_after = None   # debounce handle
            self._search_seq = 0        # ignore stale threaded results

            self._set_icon()
            self._build_menu()
            self._build_layout()
            self._apply_theme()
            self._reload_sources()
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.after(60, lambda: self.search_entry.focus_set())

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("find-all.ico")
                if ico:
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("find-all.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- theming
        def track(self, widget, role):
            self._tracked.append((widget, role))

        def _pal(self):
            return PALETTES[self.theme]

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Sidebar.TFrame", background=p["surface"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Header.TLabel", background=p["bg"], foreground=p["text"],
                            font=(FONT, 15, "bold"))
            style.configure("Brand.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 12, "bold"))
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("Search.TEntry", fieldbackground=p["entry"],
                            foreground=p["text"], insertcolor=p["text"])
            style.configure("TButton", background=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], focuscolor=p["surface"],
                            padding=(10, 5))
            style.map("TButton",
                      background=[("active", p["trough"]), ("disabled", p["bg"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(12, 6))
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("disabled", p["border"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Toggle.TButton", background=p["surface"],
                            foreground=p["text"], padding=(8, 4))
            for name in ("TEntry", "TSpinbox"):
                style.configure(name, fieldbackground=p["entry"], foreground=p["text"],
                                insertcolor=p["text"], bordercolor=p["border"])
            style.configure("TCombobox", fieldbackground=p["entry"],
                            foreground=p["text"], background=p["surface"],
                            arrowcolor=p["text"])
            style.map("TCombobox",
                      fieldbackground=[("readonly", p["entry"])],
                      foreground=[("readonly", p["text"])])
            style.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
            style.map("TCheckbutton", background=[("active", p["bg"])])
            style.configure("Treeview", background=p["surface"],
                            fieldbackground=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], rowheight=40)
            style.map("Treeview", background=[("selected", p["primary"])],
                      foreground=[("selected", p["sel_fg"])])
            style.configure("Treeview.Heading", background=p["surface"],
                            foreground=p["muted"], font=(FONT, 9, "bold"))
            style.configure("TScrollbar", background=p["surface"],
                            troughcolor=p["bg"], bordercolor=p["border"],
                            arrowcolor=p["text"])
            style.configure("Horizontal.TProgressbar", background=p["primary"],
                            troughcolor=p["trough"], bordercolor=p["border"])
            style.configure("TSeparator", background=p["border"])

            for widget, role in list(self._tracked):
                try:
                    if role == "listbox":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                    elif role == "text":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         insertbackground=p["text"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                except Exception:
                    pass

        def toggle_theme(self):
            self.theme = "dark" if self.theme == "light" else "light"
            guiconfig.set_theme(self.theme)
            self._apply_theme()
            self._theme_btn.configure(
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")

        # ---- menu
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Manage sources…", command=self._manage_sources)
            filem.add_separator()
            filem.add_command(label="Exit", command=self.destroy)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle dark mode", command=self.toggle_theme)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=self._about)
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

        # ---- layout
        def _build_layout(self):
            # top brand bar
            top = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 8))
            top.pack(fill="x", side="top")
            ttk.Label(top, text="FindAll", style="Brand.TLabel").pack(side="left")
            ttk.Label(top, style="Status.TLabel",
                      text="  offline · open source · by QuickOpen").pack(side="left")
            self._theme_btn = ttk.Button(
                top, style="Toggle.TButton", command=self.toggle_theme,
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self._theme_btn.pack(side="right")

            # search bar
            searchbar = ttk.Frame(self, style="TFrame", padding=(16, 12, 16, 6))
            searchbar.pack(fill="x")
            ttk.Label(searchbar, text="Source", style="Muted.TLabel").pack(side="left")
            self.source_var = tk.StringVar()
            self.source_combo = ttk.Combobox(searchbar, textvariable=self.source_var,
                                             state="readonly", width=20)
            self.source_combo.pack(side="left", padx=(6, 12))
            self.source_combo.bind("<<ComboboxSelected>>", lambda _e: self._trigger_search())

            self.query_var = tk.StringVar()
            self.search_entry = ttk.Entry(searchbar, textvariable=self.query_var,
                                          style="Search.TEntry", font=(FONT, 12))
            self.search_entry.pack(side="left", fill="x", expand=True)
            self.query_var.trace_add("write", lambda *_: self._on_type())

            ttk.Button(searchbar, text="Manage sources…",
                       command=self._manage_sources).pack(side="left", padx=(12, 0))

            # results table
            body = ttk.Frame(self, style="TFrame", padding=(16, 4))
            body.pack(fill="both", expand=True)
            cols = ("name", "size", "modified", "snippet")
            self.tree = ttk.Treeview(body, columns=cols, show="headings",
                                     selectmode="browse")
            for cid, label, width, stretch in (
                ("name", "Name", 220, False),
                ("size", "Size", 80, False),
                ("modified", "Modified", 130, False),
                ("snippet", "Match / path", 420, True),
            ):
                self.tree.heading(cid, text=label)
                self.tree.column(cid, width=width, stretch=stretch, anchor="w")
            sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.tree.pack(side="left", fill="both", expand=True)
            self.tree.bind("<Double-1>", lambda _e: self._open_selected())
            self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_actions())

            # actions + status bar
            bar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 6))
            bar.pack(fill="x", side="bottom")
            self.status_lbl = ttk.Label(bar, text="Ready", style="Status.TLabel",
                                        anchor="w")
            self.status_lbl.pack(side="left")
            self.open_folder_btn = ttk.Button(bar, text="Open folder",
                                              command=self._reveal_selected)
            self.open_file_btn = ttk.Button(bar, text="Open file",
                                            command=self._open_selected)
            self.open_folder_btn.pack(side="right")
            self.open_file_btn.pack(side="right", padx=6)

        # ---- sources
        def _reload_sources(self):
            names = [name for name, _info in index_mod.list_sources()]
            self.source_combo.configure(values=names)
            if names and self.source_var.get() not in names:
                self.source_var.set(names[0])
            elif not names:
                self.source_var.set("")
                self._set_status("No sources yet — use “Manage sources…” to add a folder.")

        # ---- as-you-type search (debounced + threaded)
        def _on_type(self):
            if self._search_after is not None:
                try:
                    self.after_cancel(self._search_after)
                except Exception:
                    pass
            self._search_after = self.after(220, self._trigger_search)

        def _trigger_search(self):
            self._search_after = None
            name = self.source_var.get().strip()
            query = self.query_var.get().strip()
            self._clear_results()
            if not name:
                return
            if not query:
                self._set_status("Type to search.")
                return
            self._search_seq += 1
            seq = self._search_seq
            self._set_status("Searching…", kind="working")

            def work():
                try:
                    return search_mod.search(name, query, limit=200), None
                except FindAllError as exc:
                    return None, str(exc)
                except Exception as exc:  # never leak a traceback
                    return None, f"Unexpected error: {exc}"

            def run():
                res, err = work()
                self.after(0, lambda: self._show_results(seq, res, err))

            threading.Thread(target=run, daemon=True).start()

        def _show_results(self, seq, res, err):
            if seq != self._search_seq:
                return  # a newer search superseded this one
            if err is not None:
                self._set_status(err, kind="err")
                return
            self._results = res or []
            for i, r in enumerate(self._results):
                snippet = r.get("snippet") or r.get("path") or ""
                snippet = " ".join(snippet.split())
                self.tree.insert("", "end", iid=str(i), values=(
                    r.get("name") or "",
                    human_size(r.get("size")),
                    fmt_time(r.get("mtime")),
                    snippet,
                ))
            n = len(self._results)
            self._set_status(f"{n} match(es)." if n else "No matches.",
                             kind="ok" if n else "idle")

        def _clear_results(self):
            self._results = []
            for iid in self.tree.get_children():
                self.tree.delete(iid)

        def _selected_result(self):
            sel = self.tree.selection()
            if not sel:
                return None
            try:
                return self._results[int(sel[0])]
            except (ValueError, IndexError):
                return None

        def _sync_actions(self):
            pass  # buttons are always visible; guarded on click

        def _open_selected(self):
            r = self._selected_result()
            if r and r.get("path"):
                if not open_with_default_app(r["path"]):
                    self._set_status("Could not open file.", kind="err")

        def _reveal_selected(self):
            r = self._selected_result()
            if r and r.get("path"):
                if not open_in_file_manager(r["path"]):
                    self._set_status("Could not open folder.", kind="err")

        # ---- status bar
        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"working": p["primary"], "ok": p["ok"], "err": p["err"]}.get(
                kind, p["muted"])
            self.status_lbl.configure(text=text, foreground=color)

        # ---- Manage sources dialog
        def _manage_sources(self):
            win = tk.Toplevel(self)
            win.title("Manage sources")
            win.geometry("620x520")
            win.configure(bg=self._pal()["bg"])
            win.transient(self)

            add = ttk.Labelframe(win, text="Add / re-index a folder", padding=10)
            add.pack(fill="x", padx=12, pady=(12, 6))

            row1 = ttk.Frame(add, style="TFrame")
            row1.pack(fill="x", pady=3)
            ttk.Label(row1, text="Folder", width=10, anchor="w").pack(side="left")
            folder_var = tk.StringVar()
            ttk.Entry(row1, textvariable=folder_var).pack(
                side="left", fill="x", expand=True, padx=(0, 6))

            def browse():
                d = filedialog.askdirectory(title="Choose a folder to index")
                if d:
                    folder_var.set(d)
                    if not name_var.get().strip():
                        name_var.set(os.path.basename(os.path.normpath(d)))
            ttk.Button(row1, text="Browse…", command=browse, width=10).pack(side="left")

            row2 = ttk.Frame(add, style="TFrame")
            row2.pack(fill="x", pady=3)
            ttk.Label(row2, text="Name", width=10, anchor="w").pack(side="left")
            name_var = tk.StringVar()
            ttk.Entry(row2, textvariable=name_var, width=24).pack(side="left")
            content_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(row2, text="Index file contents",
                            variable=content_var).pack(side="left", padx=(16, 0))

            row3 = ttk.Frame(add, style="TFrame")
            row3.pack(fill="x", pady=3)
            ttk.Label(row3, text="Only exts", width=10, anchor="w").pack(side="left")
            exts_var = tk.StringVar()
            ttk.Entry(row3, textvariable=exts_var).pack(
                side="left", fill="x", expand=True)
            ttk.Label(add, style="Muted.TLabel",
                      text="Space/comma-separated, e.g. “txt md py”. Blank = all files."
                      ).pack(anchor="w", pady=(2, 0))

            prog = ttk.Progressbar(add, mode="determinate",
                                   style="Horizontal.TProgressbar")
            prog.pack(fill="x", pady=(8, 2))
            prog_lbl = ttk.Label(add, text="", style="Muted.TLabel")
            prog_lbl.pack(anchor="w")

            index_btn = ttk.Button(add, text="Add & index", style="Accent.TButton")
            index_btn.pack(anchor="w", pady=(6, 0))

            existing = ttk.Labelframe(win, text="Existing sources", padding=10)
            existing.pack(fill="both", expand=True, padx=12, pady=6)
            lb = tk.Listbox(existing, height=8, activestyle="none",
                            exportselection=False)
            lb.pack(fill="both", expand=True)
            self.track(lb, "listbox")

            def refresh_list():
                lb.delete(0, "end")
                for name, info in index_mod.list_sources():
                    exts = ",".join(info.get("exts")) if info.get("exts") else "all"
                    lb.insert("end", f"{name}  —  {info.get('count', '?')} files "
                                     f"[{exts}]  {info.get('root', '')}")
            refresh_list()

            def selected_name():
                sel = lb.curselection()
                if not sel:
                    return None
                sources = index_mod.list_sources()
                if sel[0] < len(sources):
                    return sources[sel[0]][0]
                return None

            def set_progress(done, total, _path):
                def upd():
                    prog.configure(maximum=max(1, total), value=done)
                    prog_lbl.configure(text=f"Indexed {done}/{total} files")
                self.after(0, upd)

            def do_index(name=None, root=None, content=None, exts=None):
                nm = (name or name_var.get()).strip()
                rt = root or folder_var.get().strip()
                inc = content_var.get() if content is None else content
                raw = exts if exts is not None else exts_var.get()
                ex = [e for e in raw.replace(",", " ").split()] if isinstance(raw, str) else raw
                if not rt:
                    prog_lbl.configure(text="Choose a folder first.")
                    return
                if not nm:
                    prog_lbl.configure(text="Give the source a name.")
                    return
                index_btn.state(["disabled"])
                prog.configure(value=0)
                prog_lbl.configure(text="Starting…")

                def run():
                    try:
                        res = index_mod.build_index(rt, name=nm,
                                                    include_content=inc,
                                                    exts=(ex or None),
                                                    progress=set_progress)
                        err = None
                    except FindAllError as exc:
                        res, err = None, str(exc)
                    except Exception as exc:
                        res, err = None, f"Unexpected error: {exc}"

                    def done():
                        index_btn.state(["!disabled"])
                        if err:
                            prog_lbl.configure(text=err)
                        else:
                            prog_lbl.configure(
                                text=f"Done — {res['count']} files indexed.")
                            refresh_list()
                            self._reload_sources()
                            self.source_var.set(nm)
                            self._trigger_search()
                    self.after(0, done)

                threading.Thread(target=run, daemon=True).start()

            index_btn.configure(command=do_index)

            btns = ttk.Frame(existing, style="TFrame")
            btns.pack(fill="x", pady=(6, 0))

            def reindex_selected():
                nm = selected_name()
                if not nm:
                    return
                info = index_mod.get_source(nm) or {}
                folder_var.set(info.get("root", ""))
                name_var.set(nm)
                content_var.set(bool(info.get("include_content", True)))
                exts_var.set(" ".join(info.get("exts") or []))
                do_index(name=nm, root=info.get("root"),
                         content=bool(info.get("include_content", True)),
                         exts=info.get("exts") or [])

            def remove_selected():
                nm = selected_name()
                if not nm:
                    return
                try:
                    index_mod.remove_source(nm)
                except FindAllError as exc:
                    prog_lbl.configure(text=str(exc))
                    return
                refresh_list()
                self._reload_sources()

            ttk.Button(btns, text="Re-index", command=reindex_selected).pack(side="left")
            ttk.Button(btns, text="Remove", command=remove_selected).pack(
                side="left", padx=6)
            ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")

            win.grab_set()

        # ---- About
        def _about(self):
            win = tk.Toplevel(self)
            win.title("About FindAll")
            win.configure(bg=self._pal()["bg"])
            win.resizable(False, False)
            frm = ttk.Frame(win, style="TFrame", padding=18)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text="FindAll", style="Header.TLabel").pack(anchor="w")
            ttk.Label(frm, text=f"Version {APP_VERSION}",
                      style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
            ttk.Label(frm, style="TLabel", justify="left", wraplength=380,
                      text="Instant, fully-offline filename + full-text search. "
                           "Index a folder once, then search names and contents "
                           "with fuzzy matching and filters.\n\n"
                           "100% AI-built, open source, published on QuickOpen. "
                           "Nothing is ever uploaded anywhere.").pack(anchor="w")
            ttk.Label(frm, style="Muted.TLabel", justify="left", wraplength=380,
                      text="Licensed under Apache-2.0. Built on Whoosh (BSD)."
                      ).pack(anchor="w", pady=(8, 4))
            ttk.Button(frm, text="Close", command=win.destroy).pack(anchor="e")
            win.transient(self)
            win.grab_set()

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server), it prints a friendly note and returns 0
    instead of raising.
    """
    try:
        import tkinter as tk
    except Exception as exc:  # tkinter missing entirely
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except tk.TclError as exc:
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the Windows desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

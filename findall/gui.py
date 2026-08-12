#!/usr/bin/env python3
r"""FindAll -- an Aura (QuickOpen design system) GUI on top of the ``findall`` API.

A single Aura window, search-first: a **Search** section with a source
selector and an as-you-type (debounced, threaded) query box over a results
table with "Open file" / "Open folder" actions; a **Sources** section to add
a folder and (re)index it with a progress bar; and an **About** section.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``findall/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) — declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root
    window, and it degrades gracefully (prints a message, returns 0) with no
    display or with customtkinter missing.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the
    exe directory when ``sys.frozen`` is set -- never ``__file__``.
  * Indexing and search run on background threads; results are marshalled
    back with ``self.after`` and errors are shown in the Aura status bar,
    never as a traceback.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# tkinter/customtkinter are imported lazily inside main()/build_app so merely
# importing this module (e.g. during packaging or on a headless CI box) never
# fails.

APP_NAME = "FindAll"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "FindAll — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
ACCENT = "#5b86f7"      # publish/specs/find-all.json "accent": [91, 134, 247]


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
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import filedialog, ttk
    import customtkinter as ctk

    from . import aura, guiconfig, index as index_mod
    # NOTE: findall/__init__ rebinds the package attribute 'search' to the
    # search() FUNCTION, so 'from . import search' would NOT get the module.
    from .search import search as run_search
    from .errors import FindAllError

    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("find-all.png"), version=APP_VERSION,
                tagline="offline search",
                on_theme_change=guiconfig.set_theme,
                size=(1080, 680), min_size=(880, 540))

            self._results = []          # current result dicts, row-indexed
            self._search_after = None   # debounce handle
            self._search_seq = 0        # ignore stale threaded results
            self._img_refs_gui = []

            self._set_icon()
            self._build_menu()
            self.add_section("search", "Search", "⚲", self._build_search)
            self.add_section("sources", "Sources", "▤", self._build_sources)
            self.add_section("about", "About", "◉", self._build_about)
            self.show("search")
            self.set_status("Ready")
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.after(80, lambda: self.search_entry.focus_set())

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("find-all.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("find-all.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- menu (native menus stay; theme lives in the sidebar toggle too)
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Manage sources…",
                              command=lambda: self.show("sources"))
            filem.add_separator()
            filem.add_command(label="Exit", command=self.destroy)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=lambda: self.show("about"))
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

        # =================================================================
        # Search section
        # =================================================================
        def _build_search(self, frame):
            bar = ctk.CTkFrame(frame, fg_color="transparent")
            bar.pack(fill="x", pady=(0, 12))
            self.source_var = tk.StringVar()
            self.source_combo = aura.AuraCombo(
                bar, variable=self.source_var, values=[], state="readonly",
                width=190, command=lambda _v: self._trigger_search())
            self.source_combo.pack(side="left", padx=(0, 10))
            # no textvariable: CTkEntry placeholders only work without one
            self.search_entry = aura.AuraEntry(
                bar, placeholder="Search names and contents…")
            self.search_entry.pack(side="left", fill="x", expand=True)
            self.search_entry.bind("<KeyRelease>", lambda _e: self._on_type())
            aura.AuraButton(bar, "Manage sources…", kind="ghost",
                            command=lambda: self.show("sources")).pack(
                side="left", padx=(10, 0))

            body = ctk.CTkFrame(frame, fg_color="transparent")
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
                self.tree.heading(cid, text=aura.spaced(label), anchor="w")
                self.tree.column(cid, width=width, stretch=stretch, anchor="w")
            sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.tree.pack(side="left", fill="both", expand=True)
            self.tree.bind("<Double-1>", lambda _e: self._open_selected())
            self.tree.bind("<Return>", lambda _e: self._open_selected())

            aura.AuraButton(self.statusbar.actions, "Open file",
                            kind="secondary", height=30,
                            command=self._open_selected).pack(side="left")
            aura.AuraButton(self.statusbar.actions, "Open folder",
                            kind="secondary", height=30,
                            command=self._reveal_selected).pack(
                side="left", padx=(8, 0))

            self._reload_sources()

        # ---- sources plumbing
        def _reload_sources(self):
            names = [name for name, _info in index_mod.list_sources()]
            self.source_combo.configure(values=names)
            if names and self.source_var.get() not in names:
                self.source_var.set(names[0])
            elif not names:
                self.source_var.set("")
                self.set_status("No sources yet — open “Sources” to add a "
                                "folder.")

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
            query = self.search_entry.get().strip()
            self._clear_results()
            if not name:
                return
            if not query:
                self.set_status("Type to search.")
                return
            self._search_seq += 1
            seq = self._search_seq
            self.set_status("Searching…", kind="working")

            def work():
                try:
                    return run_search(name, query, limit=200), None
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
                self.set_error(err)
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
            self.set_status(f"{n} match(es)." if n else "No matches.",
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

        def _open_selected(self):
            r = self._selected_result()
            if r and r.get("path"):
                if not open_with_default_app(r["path"]):
                    self.set_error("Could not open file.")

        def _reveal_selected(self):
            r = self._selected_result()
            if r and r.get("path"):
                if not open_in_file_manager(r["path"]):
                    self.set_error("Could not open folder.")

        # =================================================================
        # Sources section (was the "Manage sources" dialog)
        # =================================================================
        def _build_sources(self, frame):
            add = aura.Card(frame, title="Add / re-index a folder")
            add.pack(fill="x", pady=(0, 14))

            row1 = ctk.CTkFrame(add.body, fg_color="transparent")
            row1.pack(fill="x", pady=(0, 8))
            self._folder_entry = aura.AuraEntry(
                row1, placeholder="Folder to index…")
            self._folder_entry.pack(side="left", fill="x", expand=True,
                                    padx=(0, 8))
            aura.AuraButton(row1, "Browse…", kind="secondary",
                            command=self._browse_folder).pack(side="left")

            row2 = ctk.CTkFrame(add.body, fg_color="transparent")
            row2.pack(fill="x", pady=(0, 8))
            self._name_entry = aura.AuraEntry(row2, placeholder="Source name",
                                              width=220)
            self._name_entry.pack(side="left", padx=(0, 14))
            self._content_var = tk.BooleanVar(value=True)
            ctk.CTkCheckBox(row2, text="Index file contents",
                            variable=self._content_var,
                            font=aura.font()).pack(side="left")

            row3 = ctk.CTkFrame(add.body, fg_color="transparent")
            row3.pack(fill="x", pady=(0, 4))
            self._exts_entry = aura.AuraEntry(
                row3, placeholder="Only extensions — e.g. “txt md py”. "
                                  "Blank = all files.")
            self._exts_entry.pack(side="left", fill="x", expand=True)

            self._prog = aura.ProgressBar(add.body)
            self._prog.pack(fill="x", pady=(10, 4))
            self._prog_lbl = aura.Caption(add.body, "")
            self._prog_lbl.pack(anchor="w")
            self._index_btn = aura.AuraButton(add.body, "Add & index",
                                              command=self._do_index)
            self._index_btn.pack(anchor="w", pady=(8, 0))

            existing = aura.Card(frame, title="Existing sources")
            existing.pack(fill="both", expand=True)
            self._src_list = tk.Listbox(existing.body, height=7,
                                        activestyle="none",
                                        exportselection=False)
            self._src_list.pack(fill="both", expand=True)
            aura.track(self._src_list, "listbox")
            btns = ctk.CTkFrame(existing.body, fg_color="transparent")
            btns.pack(fill="x", pady=(10, 0))
            aura.AuraButton(btns, "Re-index", kind="secondary",
                            command=self._reindex_selected).pack(side="left")
            aura.AuraButton(btns, "Remove", kind="danger",
                            command=self._remove_selected).pack(
                side="left", padx=(8, 0))
            self._refresh_src_list()

        @staticmethod
        def _fill(entry, text):
            entry.delete(0, "end")
            if text:
                entry.insert(0, text)

        def _browse_folder(self):
            d = filedialog.askdirectory(title="Choose a folder to index")
            if d:
                self._fill(self._folder_entry, d)
                if not self._name_entry.get().strip():
                    self._fill(self._name_entry,
                               os.path.basename(os.path.normpath(d)))

        def _refresh_src_list(self):
            self._src_list.delete(0, "end")
            for name, info in index_mod.list_sources():
                exts = ",".join(info.get("exts")) if info.get("exts") else "all"
                self._src_list.insert(
                    "end", f"{name}  —  {info.get('count', '?')} files "
                           f"[{exts}]  {info.get('root', '')}")

        def _selected_source_name(self):
            sel = self._src_list.curselection()
            if not sel:
                return None
            sources = index_mod.list_sources()
            if sel[0] < len(sources):
                return sources[sel[0]][0]
            return None

        def _set_index_progress(self, done, total, _path):
            def upd():
                self._prog.set(min(1.0, done / max(1, total)))
                self._prog_lbl.configure(text=f"Indexed {done}/{total} files")
            self.after(0, upd)

        def _do_index(self, name=None, root=None, content=None, exts=None):
            nm = (name or self._name_entry.get()).strip()
            rt = root or self._folder_entry.get().strip()
            inc = self._content_var.get() if content is None else content
            raw = exts if exts is not None else self._exts_entry.get()
            ex = ([e for e in raw.replace(",", " ").split()]
                  if isinstance(raw, str) else raw)
            if not rt:
                self.set_error("Choose a folder first.")
                return
            if not nm:
                self.set_error("Give the source a name.")
                return
            self._index_btn.state(["disabled"])
            self._prog.set(0)
            self._prog_lbl.configure(text="Starting…")
            self.set_status("Indexing…", kind="working")

            def run():
                try:
                    res = index_mod.build_index(rt, name=nm,
                                                include_content=inc,
                                                exts=(ex or None),
                                                progress=self._set_index_progress)
                    err = None
                except FindAllError as exc:
                    res, err = None, str(exc)
                except Exception as exc:
                    res, err = None, f"Unexpected error: {exc}"

                def done():
                    self._index_btn.state(["!disabled"])
                    if err:
                        self._prog_lbl.configure(text=err)
                        self.set_error(err)
                    else:
                        self._prog_lbl.configure(
                            text=f"Done — {res['count']} files indexed.")
                        self.set_success(f"Indexed {res['count']} files "
                                         f"into “{nm}”.")
                        self._refresh_src_list()
                        self._reload_sources()
                        self.source_var.set(nm)
                        self._trigger_search()
                self.after(0, done)

            threading.Thread(target=run, daemon=True).start()

        def _reindex_selected(self):
            nm = self._selected_source_name()
            if not nm:
                return
            info = index_mod.get_source(nm) or {}
            self._fill(self._folder_entry, info.get("root", ""))
            self._fill(self._name_entry, nm)
            self._content_var.set(bool(info.get("include_content", True)))
            self._fill(self._exts_entry, " ".join(info.get("exts") or []))
            self._do_index(name=nm, root=info.get("root"),
                           content=bool(info.get("include_content", True)),
                           exts=info.get("exts") or [])

        def _remove_selected(self):
            nm = self._selected_source_name()
            if not nm:
                return
            try:
                index_mod.remove_source(nm)
            except FindAllError as exc:
                self.set_error(str(exc))
                return
            self._refresh_src_list()
            self._reload_sources()
            self.set_status(f"Removed source “{nm}”.")

        # =================================================================
        # About section
        # =================================================================
        def _build_about(self, frame):
            card = aura.Card(frame, title="About FindAll")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=520,
                text="Instant, fully-offline filename + full-text search. "
                     "Index a folder once, then search names and contents "
                     "with fuzzy matching and filters.\n\n"
                     "100% AI-built, open source, published on QuickOpen. "
                     "Nothing is ever uploaded anywhere.").pack(anchor="w")
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on Whoosh (BSD) "
                         "and CustomTkinter (MIT).").pack(
                anchor="w", pady=(10, 4))
            link = aura.AuraButton(card.body, "Project page: quickopen.ai",
                                   kind="ghost",
                                   command=lambda: open_with_default_app(
                                       PROJECT_URL))
            link.pack(anchor="w", pady=(6, 0))

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server) or without customtkinter installed, it
    prints a friendly note and returns 0 instead of raising.
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
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
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
